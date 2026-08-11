/**
 * The scoring screen — a direct descendant of the prototype's LiveGame, with
 * the additions the tournament model brings: games within a match, timeouts,
 * an offline indicator and a pending-rally count.
 */

import { ArrowLeft, Check, Clock, Flag, Undo2 } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Ball, AvatarChip, Sheet, Spinner, cx } from "../components/ui";
import { useMatch } from "../lib/useMatch";
import { currentGame, currentServeSide, currentServer, scoreCall } from "../scoring/engine";
import type { MatchState, Team } from "../scoring/engine";

function ServeDiagram({ side }: { side: "R" | "L" }) {
  return (
    <div className="serve-diagram" title="Server position">
      <div className="sd-half">
        <span className={cx("sd-cell", side === "L" && "sd-active")} />
        <span className={cx("sd-cell", side === "R" && "sd-active")} />
      </div>
    </div>
  );
}

export default function LiveMatch() {
  const { matchId = "" } = useParams();
  const navigate = useNavigate();
  const match = useMatch(matchId);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [showTimeout, setShowTimeout] = useState(false);

  if (match.loading) return <Spinner label="Loading match…" />;
  if (!match.server) {
    return (
      <div className="stack">
        <div className="err">{match.error ?? "Match not found"}</div>
        <button className="btn btn-line" onClick={() => navigate(-1)}>Back</button>
      </div>
    );
  }

  const payload = match.server;
  // Local state wins when there are unsent rallies — that is the whole point of
  // the offline queue.
  const state: MatchState | null = match.local;
  const game = state ? currentGame(state) : null;
  const serverPlayer = state ? currentServer(state) : null;
  const side = state ? currentServeSide(state) : payload.current?.side ?? null;
  const call = state ? scoreCall(state) : payload.current?.call ?? null;

  const score = game?.score ?? payload.current?.score ?? { A: 0, B: 0 };
  const servingTeam = (game?.serving_team ?? payload.current?.serving_team ?? "A") as Team;
  const serverNum = game?.server_num ?? payload.current?.server_num ?? 1;
  const serverId = serverPlayer?.id ?? payload.current?.server_id ?? null;
  const gamesWon = state?.games_won ?? payload.games_won;
  const status = state?.status ?? payload.status;
  const winner = state?.winner ?? payload.winner;
  const config = payload.config as { format?: string; best_of?: number };
  const isDoubles = config.format !== "singles";
  const timeouts = game?.timeouts_used ?? payload.current?.timeouts_used ?? { A: 0, B: 0 };

  if (status === "complete" || status === "abandoned") {
    const wName = winner ? payload.teams[winner].name : null;
    const points = Object.entries(state?.serve_points ?? payload.serve_points)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4);
    const names = state?.serve_names ?? payload.serve_names;
    return (
      <div className="won">
        <div className="won-confetti"><Ball size={40} glow /></div>
        <p className="won-eyebrow">
          {status === "abandoned" ? "Match abandoned" : "Match complete"}
        </p>
        <h2 className="won-team">{wName ? `${wName} wins` : "No result"}</h2>
        <div className="won-score">
          {gamesWon.A}–{gamesWon.B}
        </div>
        <div className="game-strip">
          {(state?.games ?? payload.games).map((g) => (
            <span key={g.number} className="game-pill">
              {g.score.A}–{g.score.B}
            </span>
          ))}
        </div>
        <div className="won-leaders">
          {points.length > 0 && (
            <>
              <p className="tiny-label center">Serve points</p>
              {points.map(([id, n]) => (
                <div className="won-leader-row" key={id}>
                  <span>{names[id] ?? id}</span><b>{n}</b>
                </div>
              ))}
            </>
          )}
        </div>
        <button className="btn btn-primary btn-block btn-lg" onClick={() => navigate(-1)}>
          <Check size={18} /> Done
        </button>
      </div>
    );
  }

  const TeamBlock = ({ team }: { team: Team }) => {
    const serving = team === servingTeam;
    const roster = payload.teams[team];
    return (
      <div className={cx("score-team", serving && "serving")} data-team={team}>
        <div className="score-team-top">
          <div className="score-team-players">
            {roster.players.map((p, i) => {
              const isServer = serving && (isDoubles ? p.id === serverId : true);
              return (
                <div key={p.id} className={cx("pl-chip", isServer && "pl-serving")}>
                  <AvatarChip player={{ name: p.name }} size={26} />
                  <span>{p.name.split(" ")[0]}</span>
                  {isServer && <span className="srv-ball"><Ball size={16} glow /></span>}
                  {i === -1 && null}
                </div>
              );
            })}
          </div>
        </div>
        <div className="score-num">{score[team]}</div>
        {serving && (
          <div className="serve-tag">
            Serving · side {side === "R" ? "right" : "left"}
            {isDoubles ? ` · server ${serverNum}` : ""}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="live">
      <div className="live-head">
        <button className="icon-btn ghost-back" onClick={() => navigate(-1)} aria-label="Back">
          <ArrowLeft size={18} />
        </button>
        <span className="live-dot" /> Game {game?.number ?? 1}
        <span className="live-target">
          to {game?.target ?? payload.current?.target}
          {" · "}best of {config.best_of ?? 3}
        </span>
      </div>

      <ConnectionBar match={match} />

      <div className="games-won-row">
        <span>Games</span>
        <b>{gamesWon.A} – {gamesWon.B}</b>
      </div>

      <div className="scoreboard">
        <div className="court-bg">
          <div className="cb-line cb-net" /><div className="cb-line cb-k1" />
          <div className="cb-line cb-k2" />
        </div>
        <TeamBlock team="A" />
        <div className="score-mid">
          <div className="call">{call}</div>
          {side && <ServeDiagram side={side} />}
        </div>
        <TeamBlock team="B" />
      </div>

      <div className="serving-banner">
        <Ball size={18} />
        <span>
          <b>{payload.teams[servingTeam].name}</b> serving
          {serverPlayer ? ` — ${serverPlayer.name.split(" ")[0]}` : ""}
          {side ? ` from the ${side === "R" ? "right" : "left"}` : ""}
        </span>
      </div>

      <div className="result-btns">
        <button className="btn-result btn-point" onClick={() => void match.send("RALLY_WON")}>
          <span className="br-big">Point</span>
          <span className="br-sub">serving team won the rally</span>
        </button>
        <button className="btn-result btn-sideout" onClick={() => void match.send("RALLY_LOST")}>
          <span className="br-big">{isDoubles ? "Side out" : "Point to receiver"}</span>
          <span className="br-sub">serving team lost the rally</span>
        </button>
      </div>

      <div className="live-actions">
        <button className="btn btn-line btn-sm" onClick={() => void match.send("UNDO")}>
          <Undo2 size={16} /> Undo last
        </button>
        <button className="btn btn-line btn-sm" onClick={() => setShowTimeout(true)}>
          <Clock size={15} /> Timeout
        </button>
        <button className="btn btn-line btn-sm danger" onClick={() => setConfirmEnd(true)}>
          <Flag size={15} /> End early
        </button>
      </div>

      {match.error && <div className="err">{match.error}</div>}

      <Sheet open={showTimeout} onClose={() => setShowTimeout(false)} title="Call a timeout">
        <p className="sheet-text">
          Each team gets {(payload.config as { timeouts_per_game?: number })
            .timeouts_per_game ?? 2} per game.
        </p>
        <div className="sheet-btns">
          {(["A", "B"] as Team[]).map((team) => (
            <button
              key={team}
              className="btn btn-line"
              onClick={() => { void match.send("TIMEOUT", team); setShowTimeout(false); }}
            >
              {payload.teams[team].name} ({timeouts[team]} used)
            </button>
          ))}
        </div>
      </Sheet>

      <Sheet open={confirmEnd} onClose={() => setConfirmEnd(false)} title="End match early?">
        <p className="sheet-text">
          The score so far is kept and no winner is recorded. A forfeit instead
          awards the match to the other team.
        </p>
        <div className="sheet-btns">
          <button className="btn btn-ghost" onClick={() => setConfirmEnd(false)}>Cancel</button>
          <button
            className="btn btn-primary danger-fill"
            onClick={() => { void match.send("END_EARLY"); setConfirmEnd(false); }}
          >
            End match
          </button>
        </div>
        <div className="sheet-btns">
          {(["A", "B"] as Team[]).map((team) => (
            <button
              key={team}
              className="btn btn-line btn-sm"
              onClick={() => { void match.send("FORFEIT", team); setConfirmEnd(false); }}
            >
              {payload.teams[team].name} forfeits
            </button>
          ))}
        </div>
      </Sheet>
    </div>
  );
}

function ConnectionBar({ match }: { match: ReturnType<typeof useMatch> }) {
  if (match.notice) {
    return (
      <div className="sync-bar warn">
        <span>{match.notice}</span>
        <button className="icon-btn sm" onClick={match.dismissNotice}>×</button>
      </div>
    );
  }
  if (match.connection === "offline") {
    return (
      <div className="sync-bar offline">
        Offline — scoring locally
        {match.pending > 0 && <b>{match.pending} rally{match.pending === 1 ? "" : "s"} queued</b>}
      </div>
    );
  }
  if (match.pending > 0) {
    return <div className="sync-bar syncing">Syncing {match.pending}…</div>;
  }
  return null;
}
