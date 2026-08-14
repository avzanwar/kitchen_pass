/** Typed fetch wrapper and the API surface the UI uses. */

const TOKEN_KEY = "kp:token";

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const setToken = (token: string): void => localStorage.setItem(TOKEN_KEY, token);
export const clearToken = (): void => localStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body?: unknown,
  ) {
    super(message);
  }

  /** True when the server rejected the request as illegal rather than failing. */
  get isConflict(): boolean {
    return this.status === 409;
  }
}

export class OfflineError extends Error {
  constructor() {
    super("You are offline");
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  // FormData must set its own Content-Type: the multipart boundary is generated
  // by the browser, and overriding the header leaves the server unable to split
  // the parts.
  if (!(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, { ...init, headers });
  } catch {
    // fetch only rejects on a network-level failure, which is the signal the
    // outbox uses to decide "queue it, don't drop it".
    throw new OfflineError();
  }

  if (response.status === 401) {
    clearToken();
    throw new ApiError("Your session has expired — sign in again", 401);
  }
  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const body: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new ApiError(detailOf(body) ?? response.statusText, response.status, body);
  }
  return body as T;
}

function detailOf(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    // A structured rejection — the bulk import returns { message, preview }.
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  if (Array.isArray(detail)) {
    // FastAPI validation errors: surface the first message rather than [object].
    const first = detail[0] as { msg?: string; loc?: string[] } | undefined;
    if (first?.msg) {
      const field = first.loc?.slice(1).join(".");
      return field ? `${field}: ${first.msg}` : first.msg;
    }
  }
  return null;
}

/** FormData rides through untouched; anything else is JSON. */
function encodeBody(body: unknown): BodyInit | undefined {
  if (body === undefined) return undefined;
  if (body instanceof FormData) return body;
  return JSON.stringify(body);
}

const get = <T,>(path: string) => request<T>(path);
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: encodeBody(body) });
const patch = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "PATCH", body: encodeBody(body) });
const del = (path: string) => request<void>(path, { method: "DELETE" });

function importForm(
  file: File,
  target: { tournamentId?: string; name?: string },
): FormData {
  const form = new FormData();
  form.append("file", file);
  if (target.tournamentId) form.append("tournament_id", target.tournamentId);
  if (target.name) form.append("tournament_name", target.name);
  return form;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: string;
  is_active: boolean;
}

export interface Avatar {
  type: "initials" | "emoji" | "photo";
  value?: string;
  color?: string;
}

export interface Player {
  id: string;
  name: string;
  avatar?: Avatar | null;
  rating?: number | null;
}

export interface Tournament {
  id: string;
  name: string;
  slug: string;
  owner_id: string;
  starts_on: string | null;
  ends_on: string | null;
  timezone: string;
  status: string;
  public_token: string;
}

export interface Court {
  id: string;
  tournament_id: string;
  name: string;
  status: string;
  sort_order: number;
}

export interface Division {
  id: string;
  tournament_id: string;
  name: string;
  format: string;
  skill_bracket: string | null;
  age_bracket: string | null;
  draw_kind: string;
  draw_config: Record<string, unknown>;
  match_config: Record<string, unknown>;
  tiebreakers: string[];
  draw_generated: boolean;
}

export interface Entry {
  id: string;
  division_id: string;
  name: string;
  seed: number | null;
  status: string;
  players: Player[];
}

export interface DrawMatch {
  id: string;
  division_id: string;
  draw_match_id: string;
  bracket: string;
  round: number;
  slot: number;
  pool: string | null;
  label: string | null;
  status: string;
  entry_a_id: string | null;
  entry_b_id: string | null;
  entry_a_name: string | null;
  entry_b_name: string | null;
  a_label: string;
  b_label: string;
  court_id: string | null;
  winner_entry_id: string | null;
  decides_title: boolean;
}

export interface Draw {
  division_id: string;
  kind: string;
  pools: Record<string, string[]>;
  matches: DrawMatch[];
}

export interface StandingRow {
  entry_id: string;
  entry_name: string;
  rank: number;
  played: number;
  wins: number;
  losses: number;
  games_won: number;
  games_lost: number;
  points_for: number;
  points_against: number;
  point_diff: number;
  unresolved_tie: boolean;
  decided_by: string | null;
}

export interface StandingsTable {
  pool: string | null;
  rows: StandingRow[];
}

export interface MatchStatePayload {
  match_id: string;
  division_id: string;
  status: string;
  winner: "A" | "B" | null;
  winner_entry_id: string | null;
  ended_early: boolean;
  forfeited_by: string | null;
  games_won: Record<string, number>;
  config: Record<string, unknown>;
  teams: Record<"A" | "B", { name: string; players: { id: string; name: string }[]; entry_id: string | null }>;
  games: {
    number: number; target: number; score: Record<string, number>;
    status: string; winner: string | null;
  }[];
  current: {
    number: number;
    target: number;
    score: Record<"A" | "B", number>;
    serving_team: "A" | "B";
    server_id: string | null;
    server_name: string | null;
    server_num: number;
    side: "R" | "L";
    call: string;
    pos: Record<"A" | "B", number[]>;
    timeouts_used: Record<"A" | "B", number>;
    ends_swapped: boolean;
  } | null;
  serve_points: Record<string, number>;
  serve_names: Record<string, string>;
  seq?: number;
  lease?: { scorekeeper_id: string | null; held_by_other: boolean; expires_at: string | null };
}

export interface BoardCourt {
  id: string;
  name: string;
  status: string;
  matches: {
    match_id: string; division_id: string; division_name: string;
    draw_match_id: string; label: string | null; status: string; a: string; b: string;
  }[];
}

export interface Board {
  courts: BoardCourt[];
  queue: BoardCourt["matches"];
  conflicts: { match_id: string; reason: string; player_ids: string[] }[];
}

export interface ImportProblem {
  severity: "error" | "warning";
  message: string;
  /** Line number in the uploaded sheet; null for a problem with the whole file. */
  row: number | null;
}

export interface ImportPreview {
  ok: boolean;
  tournament_name: string;
  tournament_id: string | null;
  creates_tournament: boolean;
  divisions: {
    name: string;
    format: string;
    draw_kind: string;
    skill: string | null;
    age: string | null;
    best_of: number;
    pools: number;
    existing: boolean;
    entries: {
      row: number;
      name: string;
      seed: number | null;
      players: { name: string; rating: number | null; existing: boolean }[];
    }[];
  }[];
  problems: ImportProblem[];
  entry_count: number;
  new_players: number;
  matched_players: number;
}

export interface ImportResult {
  tournament: Tournament;
  tournament_created: boolean;
  divisions_created: number;
  divisions_reused: number;
  entries_created: number;
  players_created: number;
  players_matched: number;
  problems: ImportProblem[];
}

/** The preview a rejected commit carries back, if the failure was the sheet's. */
export function previewFromError(error: unknown): ImportPreview | null {
  if (!(error instanceof ApiError)) return null;
  const detail = (error.body as { detail?: unknown } | undefined)?.detail;
  if (!detail || typeof detail !== "object") return null;
  const preview = (detail as { preview?: unknown }).preview;
  return preview && typeof preview === "object" ? (preview as ImportPreview) : null;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  register: (email: string, password: string, display_name: string) =>
    post<{ access_token: string }>("/auth/register", { email, password, display_name }),
  login: (email: string, password: string) =>
    post<{ access_token: string }>("/auth/login", { email, password }),
  me: () => get<User>("/auth/me"),

  players: () => get<Player[]>("/players"),
  createPlayer: (body: Partial<Player>) => post<Player>("/players", body),
  updatePlayer: (id: string, body: Partial<Player>) => patch<Player>(`/players/${id}`, body),
  deletePlayer: (id: string) => del(`/players/${id}`),

  tournaments: () => get<Tournament[]>("/tournaments"),
  tournament: (id: string) => get<Tournament>(`/tournaments/${id}`),
  createTournament: (body: { name: string; starts_on?: string; ends_on?: string }) =>
    post<Tournament>("/tournaments", body),
  deleteTournament: (id: string) => del(`/tournaments/${id}`),

  courts: (tid: string) => get<Court[]>(`/tournaments/${tid}/courts`),
  createCourt: (tid: string, name: string, sort_order = 0) =>
    post<Court>(`/tournaments/${tid}/courts`, { name, sort_order }),
  deleteCourt: (id: string) => del(`/courts/${id}`),

  divisions: (tid: string) => get<Division[]>(`/tournaments/${tid}/divisions`),
  division: (id: string) => get<Division>(`/divisions/${id}`),
  createDivision: (tid: string, body: Record<string, unknown>) =>
    post<Division>(`/tournaments/${tid}/divisions`, body),
  deleteDivision: (id: string) => del(`/divisions/${id}`),

  entries: (did: string) => get<Entry[]>(`/divisions/${did}/entries`),
  createEntry: (did: string, body: { player_ids: string[]; name?: string; seed?: number }) =>
    post<Entry>(`/divisions/${did}/entries`, body),
  setEntryStatus: (id: string, status: string) =>
    patch<Entry>(`/entries/${id}/status`, { status }),

  draw: (did: string) => get<Draw>(`/divisions/${did}/draw`),
  generateDraw: (did: string, replace = false) =>
    post<Draw>(`/divisions/${did}/draw?replace=${replace}`),
  standings: (did: string) => get<StandingsTable[]>(`/divisions/${did}/standings`),

  match: (id: string) => get<MatchStatePayload>(`/matches/${id}`),
  postEvents: (id: string, events: { type: string; team?: string | null; client_event_id: string }[]) =>
    post<MatchStatePayload>(`/matches/${id}/events`, { events }),
  toss: (id: string, first_server: "A" | "B") =>
    post<{ first_server: string }>(`/matches/${id}/toss`, { first_server }),
  claimMatch: (id: string, force = false) =>
    post<{ expires_at: string }>(`/matches/${id}/claim`, { force }),
  setCourt: (matchId: string, courtId: string | null) =>
    patch<unknown>(`/matches/${matchId}/court${courtId ? `?court_id=${courtId}` : ""}`),

  // Bulk import. The templates are plain links rather than fetches — a link
  // cannot carry the bearer token, and these endpoints are public because the
  // files they return are generated samples with no user data in them.
  templateUrl: (kind: "csv" | "xlsx") => `/api/v1/imports/template.${kind}`,
  importPreview: (file: File, target: { tournamentId?: string; name?: string }) =>
    post<ImportPreview>("/imports/preview", importForm(file, target)),
  importCommit: (file: File, target: { tournamentId?: string; name?: string }) =>
    post<ImportResult>("/imports/commit", importForm(file, target)),

  board: (tid: string) => get<Board>(`/tournaments/${tid}/board`),
  assignCourts: (tid: string, body: { division_id?: string; dry_run?: boolean; rest_waves?: number }) =>
    post<{ waves: number; assignments: unknown[]; unplaced: unknown[] }>(
      `/tournaments/${tid}/assign-courts`, body,
    ),

  publicOverview: (token: string) => get<{
    id: string; name: string; status: string; starts_on: string | null;
    divisions: { id: string; name: string; format: string; draw_kind: string; draw_generated: boolean }[];
  }>(`/public/${token}`),
  publicDivision: (token: string, did: string) => get<{
    id: string; name: string; pools: Record<string, string[]>;
    matches: { id: string; draw_match_id: string; bracket: string; round: number; pool: string | null;
      label: string | null; status: string; court: string | null; a: string; b: string;
      winner: string | null; games: { n: number; a: number; b: number }[] }[];
    standings: { pool: string | null; rows: (StandingRow & { entry_name: string })[] }[];
  }>(`/public/${token}/divisions/${did}`),
  publicBoard: (token: string) => get<{
    tournament: string;
    courts: { id: string; name: string; matches: { match_id: string; division: string; a: string; b: string; status: string }[] }[];
  }>(`/public/${token}/board`),
};
