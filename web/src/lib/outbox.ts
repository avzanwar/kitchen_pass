/**
 * Offline-first rally queue.
 *
 * Court wifi is unreliable, so the scorekeeper must never be blocked by the
 * network. Every rally is written to IndexedDB *first*, the UI folds it locally
 * with the mirrored engine, and a background flusher pushes to the server when
 * it can.
 *
 * The whole thing is safe to retry because each event carries a
 * `client_event_id` and the server dedupes on it — see the unique constraint
 * `uq_event_client_id`. A flush that succeeds but whose response is lost simply
 * replays and is ignored.
 */

import Dexie, { type Table } from "dexie";

import { ApiError, OfflineError, api } from "./api";
import type { ScoreEvent } from "../scoring/engine";

export interface QueuedEvent {
  /** Also the server's dedup key. */
  client_event_id: string;
  match_id: string;
  type: string;
  team?: string | null;
  /** Client-side ordering within a match. */
  local_seq: number;
  created_at: number;
}

export interface CachedMatch {
  match_id: string;
  /** Last authoritative payload the server sent, as a JSON string. */
  payload: string;
  updated_at: number;
}

class KitchenPassDB extends Dexie {
  outbox!: Table<QueuedEvent, string>;
  matches!: Table<CachedMatch, string>;

  constructor() {
    super("kitchen-pass");
    this.version(1).stores({
      outbox: "client_event_id, match_id, local_seq",
      matches: "match_id",
    });
  }
}

export const db = new KitchenPassDB();

export const newEventId = (): string =>
  globalThis.crypto?.randomUUID?.() ??
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

export async function enqueue(
  matchId: string,
  event: { type: string; team?: string | null },
): Promise<QueuedEvent> {
  const pending = await db.outbox.where("match_id").equals(matchId).toArray();
  const localSeq = pending.reduce((max, e) => Math.max(max, e.local_seq), 0) + 1;

  const queued: QueuedEvent = {
    client_event_id: newEventId(),
    match_id: matchId,
    type: event.type,
    team: event.team ?? null,
    local_seq: localSeq,
    created_at: Date.now(),
  };
  await db.outbox.add(queued);
  return queued;
}

export async function pendingFor(matchId: string): Promise<QueuedEvent[]> {
  const rows = await db.outbox.where("match_id").equals(matchId).toArray();
  return rows.sort((a, b) => a.local_seq - b.local_seq);
}

export async function pendingCount(): Promise<number> {
  return db.outbox.count();
}

export const toScoreEvents = (queued: QueuedEvent[]): ScoreEvent[] =>
  queued.map((e) => ({
    type: e.type as ScoreEvent["type"],
    team: (e.team ?? null) as ScoreEvent["team"],
    client_event_id: e.client_event_id,
  }));

export async function cacheMatch(matchId: string, payload: unknown): Promise<void> {
  await db.matches.put({
    match_id: matchId,
    payload: JSON.stringify(payload),
    updated_at: Date.now(),
  });
}

export async function cachedMatch<T>(matchId: string): Promise<T | null> {
  const row = await db.matches.get(matchId);
  return row ? (JSON.parse(row.payload) as T) : null;
}

export interface FlushResult {
  matchId: string;
  flushed: number;
  /** Set when the server refused the batch — the events were dropped. */
  rejected?: string;
  payload?: unknown;
}

/**
 * Push one match's queued events.
 *
 * Three outcomes:
 * - success: the events are removed from the queue and the fresh state cached.
 * - offline: the queue is left alone; we try again later.
 * - rejected (409): the server says these events are illegal — usually because
 *   the match was scored or finished on another device. Keeping them would
 *   retry forever, so they are discarded and the caller is told to resync.
 */
export async function flushMatch(matchId: string): Promise<FlushResult> {
  const queued = await pendingFor(matchId);
  if (queued.length === 0) return { matchId, flushed: 0 };

  try {
    const payload = await api.postEvents(
      matchId,
      queued.map((e) => ({
        type: e.type,
        team: e.team ?? null,
        client_event_id: e.client_event_id,
      })),
    );
    await db.outbox.bulkDelete(queued.map((e) => e.client_event_id));
    await cacheMatch(matchId, payload);
    return { matchId, flushed: queued.length, payload };
  } catch (error) {
    if (error instanceof OfflineError) throw error;
    if (error instanceof ApiError && error.isConflict) {
      await db.outbox.bulkDelete(queued.map((e) => e.client_event_id));
      return { matchId, flushed: 0, rejected: error.message };
    }
    throw error;
  }
}

export async function flushAll(): Promise<FlushResult[]> {
  const all = await db.outbox.toArray();
  const matchIds = [...new Set(all.map((e) => e.match_id))];
  const results: FlushResult[] = [];
  for (const matchId of matchIds) {
    try {
      results.push(await flushMatch(matchId));
    } catch (error) {
      if (error instanceof OfflineError) break; // still offline; stop early
      throw error;
    }
  }
  return results;
}

/**
 * Retry the queue on reconnect, with backoff.
 *
 * `online` events are not fully reliable (the OS can report a connection that
 * cannot reach the server), so this also polls on a slow timer as a safety net.
 */
export function startFlusher(
  onResult: (results: FlushResult[]) => void,
  { minDelay = 2000, maxDelay = 60_000 } = {},
): () => void {
  let delay = minDelay;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;

  const run = async (): Promise<void> => {
    if (stopped) return;
    try {
      const count = await pendingCount();
      if (count > 0) {
        const results = await flushAll();
        if (results.some((r) => r.flushed > 0 || r.rejected)) onResult(results);
      }
      delay = minDelay;
    } catch {
      delay = Math.min(delay * 2, maxDelay);
    }
    timer = setTimeout(run, delay);
  };

  const kick = (): void => {
    delay = minDelay;
    if (timer) clearTimeout(timer);
    void run();
  };

  window.addEventListener("online", kick);
  void run();

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    window.removeEventListener("online", kick);
  };
}
