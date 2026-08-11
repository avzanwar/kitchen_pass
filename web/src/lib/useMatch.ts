/**
 * The offline-first scoring hook.
 *
 * Reads the server's authoritative state, then folds any locally queued rallies
 * on top of it with the mirrored engine. That is what lets the scoreboard stay
 * responsive with no signal: taps land in IndexedDB, the display updates
 * immediately, and the flusher reconciles later.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, OfflineError, api, type MatchStatePayload } from "./api";
import {
  cacheMatch,
  cachedMatch,
  enqueue,
  flushMatch,
  pendingFor,
  startFlusher,
  toScoreEvents,
} from "./outbox";
import {
  InvalidEvent,
  applyEvent,
  currentGame,
  currentServeSide,
  currentServer,
  defaultConfig,
  resolveUndos,
  scoreCall,
  type MatchConfig,
  type MatchState,
  type ScoreEvent,
  type Team,
  type TeamRoster,
} from "../scoring/engine";

export type Connection = "live" | "offline" | "syncing";

export interface UseMatch {
  server: MatchStatePayload | null;
  /** Server state with local pending rallies folded on top. */
  local: MatchState | null;
  pending: number;
  connection: Connection;
  error: string | null;
  notice: string | null;
  loading: boolean;
  send: (type: string, team?: Team | null) => Promise<void>;
  refresh: () => Promise<void>;
  dismissNotice: () => void;
}

/** Rebuild engine inputs from the server payload. */
function inputsFrom(payload: MatchStatePayload): {
  config: MatchConfig;
  teams: Record<Team, TeamRoster>;
} {
  return {
    config: defaultConfig(payload.config as Partial<MatchConfig>),
    teams: {
      A: { name: payload.teams.A.name, players: payload.teams.A.players },
      B: { name: payload.teams.B.name, players: payload.teams.B.players },
    },
  };
}

export function useMatch(matchId: string): UseMatch {
  const [server, setServer] = useState<MatchStatePayload | null>(null);
  const [local, setLocal] = useState<MatchState | null>(null);
  const [pending, setPending] = useState(0);
  const [connection, setConnection] = useState<Connection>("live");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const baseEvents = useRef<ScoreEvent[]>([]);

  /** Fold the queued events on top of a server payload. */
  const recompute = useCallback(async (payload: MatchStatePayload) => {
    const queued = await pendingFor(payload.match_id);
    setPending(queued.length);

    if (queued.length === 0) {
      setLocal(null);
      return;
    }

    // The server payload is a snapshot, not a log, so replay the queued events
    // from a state reconstructed out of it.
    const { config, teams } = inputsFrom(payload);
    let state = rebuild(payload, config, teams);
    for (const event of resolveUndos(toScoreEvents(queued))) {
      try {
        state = applyEvent(state, event);
      } catch (err) {
        if (err instanceof InvalidEvent) break;
        throw err;
      }
    }
    setLocal(state);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const payload = await api.match(matchId);
      await cacheMatch(matchId, payload);
      setServer(payload);
      baseEvents.current = [];
      await recompute(payload);
      setConnection("live");
      setError(null);
    } catch (err) {
      if (err instanceof OfflineError) {
        const cached = await cachedMatch<MatchStatePayload>(matchId);
        if (cached) {
          setServer(cached);
          await recompute(cached);
        }
        setConnection("offline");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(String(err));
      }
    } finally {
      setLoading(false);
    }
  }, [matchId, recompute]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Background flush: retries the queue and folds the fresh server state in.
  useEffect(() => {
    const stop = startFlusher((results) => {
      for (const result of results) {
        if (result.matchId !== matchId) continue;
        if (result.rejected) {
          setNotice(
            `This match was updated elsewhere, so your unsent rallies were ` +
              `discarded. Showing the server's score. (${result.rejected})`,
          );
          void refresh();
        } else if (result.payload) {
          const payload = result.payload as MatchStatePayload;
          setServer(payload);
          void recompute(payload);
          setConnection("live");
        }
      }
    });
    return stop;
  }, [matchId, refresh, recompute]);

  useEffect(() => {
    const online = () => setConnection("syncing");
    const offline = () => setConnection("offline");
    window.addEventListener("online", online);
    window.addEventListener("offline", offline);
    if (!navigator.onLine) setConnection("offline");
    return () => {
      window.removeEventListener("online", online);
      window.removeEventListener("offline", offline);
    };
  }, []);

  const send = useCallback(
    async (type: string, team: Team | null = null) => {
      if (!server) return;
      setError(null);

      // Validate locally first so an illegal tap is refused instantly rather
      // than queued and rejected minutes later.
      const { config, teams } = inputsFrom(server);
      const queued = await pendingFor(matchId);
      let state = rebuild(server, config, teams);
      const events = resolveUndos([
        ...toScoreEvents(queued),
        { type: type as ScoreEvent["type"], team },
      ]);
      try {
        state = rebuild(server, config, teams);
        for (const event of events) state = applyEvent(state, event);
      } catch (err) {
        if (err instanceof InvalidEvent) {
          setError(err.message);
          return;
        }
        throw err;
      }

      await enqueue(matchId, { type, team });
      setLocal(state);
      setPending(queued.length + 1);

      try {
        const result = await flushMatch(matchId);
        if (result.rejected) {
          setNotice(
            `The server rejected that: ${result.rejected}. Reloading the ` +
              `authoritative score.`,
          );
          await refresh();
          return;
        }
        if (result.payload) {
          const payload = result.payload as MatchStatePayload;
          setServer(payload);
          await recompute(payload);
          setConnection("live");
        }
      } catch (err) {
        // Offline is expected and fine — the rally is safely in IndexedDB.
        if (err instanceof OfflineError) setConnection("offline");
        else setError(err instanceof Error ? err.message : String(err));
      }
    },
    [matchId, server, refresh, recompute],
  );

  return {
    server,
    local,
    pending,
    connection,
    error,
    notice,
    loading,
    send,
    refresh,
    dismissNotice: () => setNotice(null),
  };
}

/**
 * Reconstruct engine state from a server snapshot.
 *
 * The server sends folded state, not the log, so this rehydrates a MatchState
 * that the mirrored engine can keep folding onto. Only fields the engine reads
 * matter here.
 */
function rebuild(
  payload: MatchStatePayload,
  config: MatchConfig,
  teams: Record<Team, TeamRoster>,
): MatchState {
  return {
    config,
    teams,
    games: payload.games.map((g) => {
      const live = payload.current && payload.current.number === g.number
        ? payload.current
        : null;
      return {
        number: g.number,
        target: g.target,
        score: { A: g.score.A ?? 0, B: g.score.B ?? 0 },
        serving_team: (live?.serving_team ?? "A") as Team,
        server_idx: live
          ? teams[live.serving_team].players.findIndex((p) => p.id === live.server_id)
          : 0,
        server_num: live?.server_num ?? 1,
        pos: live?.pos ?? { A: [0, 1], B: [0, 1] },
        timeouts_used: live?.timeouts_used ?? { A: 0, B: 0 },
        technicals: { A: 0, B: 0 },
        ends_swapped: live?.ends_swapped ?? false,
        switched_at_midpoint: false,
        status: g.status,
        winner: (g.winner ?? null) as Team | null,
      };
    }),
    games_won: {
      A: payload.games_won.A ?? 0,
      B: payload.games_won.B ?? 0,
    },
    serve_points: { ...payload.serve_points },
    serve_names: { ...payload.serve_names },
    status: payload.status,
    winner: payload.winner,
    ended_early: payload.ended_early,
    forfeited_by: (payload.forfeited_by ?? null) as Team | null,
  };
}

export const derived = { currentGame, currentServer, currentServeSide, scoreCall };
