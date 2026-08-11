/**
 * TypeScript mirror of `server/app/scoring/engine.py`.
 *
 * The Python engine is authoritative. This exists so the scorekeeper can run a
 * whole match with no connectivity: the same fold, applied to the same event
 * log, must produce the same state.
 *
 * The two are held together by `conformance/corpus.json`, replayed by
 * `tests/conformance.test.ts` here and `test_conformance.py` on the server. If
 * you change behaviour in one, change it in the other and regenerate the
 * corpus — a divergence here is a release blocker, not a warning.
 */

export type Team = "A" | "B";
export type Side = "R" | "L";
export type Format = "singles" | "doubles";
export type ScoringMode = "sideout" | "rally";
export type SwitchEndsRule = "never" | "deciding_game" | "every_game";
export type FirstServerRule = "alternate" | "winner" | "loser";

export const EventType = {
  RALLY_WON: "RALLY_WON",
  RALLY_LOST: "RALLY_LOST",
  TIMEOUT: "TIMEOUT",
  TECHNICAL_WARNING: "TECHNICAL_WARNING",
  UNDO: "UNDO",
  SET_FIRST_SERVER: "SET_FIRST_SERVER",
  END_EARLY: "END_EARLY",
  FORFEIT: "FORFEIT",
} as const;
export type EventTypeName = (typeof EventType)[keyof typeof EventType];

/** Events an UNDO may pop. Terminal outcomes are not undoable. */
const UNDOABLE = new Set<EventTypeName>([
  EventType.RALLY_WON,
  EventType.RALLY_LOST,
  EventType.TIMEOUT,
  EventType.TECHNICAL_WARNING,
  EventType.SET_FIRST_SERVER,
]);

export interface ScoreEvent {
  type: EventTypeName;
  team?: Team | null;
  client_event_id?: string;
  seq?: number;
}

export interface PlayerRef {
  id: string;
  name: string;
}

export interface TeamRoster {
  name: string;
  players: PlayerRef[];
}

export interface MatchConfig {
  format: Format;
  scoring: ScoringMode;
  best_of: number;
  games_to: number[];
  win_by_2: boolean;
  freeze_at: number | null;
  timeouts_per_game: number;
  switch_ends: SwitchEndsRule;
  first_server: Team;
  first_server_rule: FirstServerRule;
}

export const defaultConfig = (over: Partial<MatchConfig> = {}): MatchConfig => ({
  format: "doubles",
  scoring: "sideout",
  best_of: 3,
  games_to: [11, 11, 15],
  win_by_2: true,
  freeze_at: null,
  timeouts_per_game: 2,
  switch_ends: "deciding_game",
  first_server: "A",
  first_server_rule: "alternate",
  ...over,
});

export interface GameState {
  number: number;
  target: number;
  score: Record<Team, number>;
  serving_team: Team;
  server_idx: number;
  server_num: number;
  pos: Record<Team, number[]>;
  timeouts_used: Record<Team, number>;
  technicals: Record<Team, number>;
  ends_swapped: boolean;
  switched_at_midpoint: boolean;
  status: string;
  winner: Team | null;
}

export interface MatchState {
  config: MatchConfig;
  teams: Record<Team, TeamRoster>;
  games: GameState[];
  games_won: Record<Team, number>;
  serve_points: Record<string, number>;
  serve_names: Record<string, string>;
  status: string;
  winner: Team | null;
  ended_early: boolean;
  forfeited_by: Team | null;
}

export class InvalidEvent extends Error {}

const other = (team: Team): Team => (team === "A" ? "B" : "A");

const targetForGame = (gamesTo: number[], n: number): number => {
  if (gamesTo.length === 0) throw new Error("games_to must not be empty");
  return gamesTo[Math.min(n - 1, gamesTo.length - 1)];
};

const gamesNeeded = (bestOf: number): number => Math.floor(bestOf / 2) + 1;

const isGameOver = (
  scorer: number,
  opp: number,
  target: number,
  winBy2: boolean,
): boolean => scorer >= target && (!winBy2 || scorer - opp >= 2);

/** Rule 12.A.2: 6 in a game to 11, 8 to 15, 11 to 21. */
export const midpoint = (target: number): number => Math.floor((target + 1) / 2);

const isFrozen = (score: number, freezeAt: number | null): boolean =>
  freezeAt !== null && score >= freezeAt;

const nextGameFirstServer = (
  rule: FirstServerRule,
  previousFirstServer: Team,
  previousWinner: Team | null,
): Team => {
  if (rule === "winner" && previousWinner) return previousWinner;
  if (rule === "loser" && previousWinner) return other(previousWinner);
  return other(previousFirstServer);
};

/** Collapse UNDO events into the effective sequence. No redo. */
export function resolveUndos(events: ScoreEvent[]): ScoreEvent[] {
  const effective: ScoreEvent[] = [];
  for (const event of events) {
    if (event.type !== EventType.UNDO) {
      effective.push(event);
      continue;
    }
    for (let i = effective.length - 1; i >= 0; i -= 1) {
      if (UNDOABLE.has(effective[i].type)) {
        effective.splice(i, 1);
        break;
      }
    }
  }
  return effective;
}

function newGame(config: MatchConfig, n: number, firstServer: Team): GameState {
  const doublesSideout = config.format === "doubles" && config.scoring === "sideout";
  return {
    number: n,
    target: targetForGame(config.games_to, n),
    score: { A: 0, B: 0 },
    serving_team: firstServer,
    server_idx: 0,
    // The game's first server is numbered 2, so the first fault is a side out.
    server_num: doublesSideout ? 2 : 1,
    pos: { A: [0, 1], B: [0, 1] },
    timeouts_used: { A: 0, B: 0 },
    technicals: { A: 0, B: 0 },
    // Teams change ends between games (Rule 12.A).
    ends_swapped: (n - 1) % 2 === 1,
    switched_at_midpoint: false,
    status: "live",
    winner: null,
  };
}

export function newMatch(
  config: MatchConfig,
  teams: Record<Team, TeamRoster>,
): MatchState {
  const need = config.format === "doubles" ? 2 : 1;
  for (const side of ["A", "B"] as Team[]) {
    if (!teams[side]) throw new Error(`missing roster for team ${side}`);
    if (teams[side].players.length !== need) {
      throw new Error(
        `team ${side} needs ${need} player(s) for ${config.format}, ` +
          `got ${teams[side].players.length}`,
      );
    }
  }
  const ids = (["A", "B"] as Team[]).flatMap((s) => teams[s].players.map((p) => p.id));
  if (new Set(ids).size !== ids.length) {
    throw new Error("the same player id appears more than once in the match");
  }

  return {
    config,
    teams,
    games: [newGame(config, 1, config.first_server)],
    games_won: { A: 0, B: 0 },
    serve_points: {},
    serve_names: {},
    status: "live",
    winner: null,
    ended_early: false,
    forfeited_by: null,
  };
}

export function currentGame(state: MatchState): GameState | null {
  if (state.games.length === 0) return null;
  const game = state.games[state.games.length - 1];
  return game.status === "live" ? game : null;
}

export function currentServer(state: MatchState): PlayerRef | null {
  const game = currentGame(state);
  if (!game) return null;
  const roster = state.teams[game.serving_team].players;
  return state.config.format === "singles" ? roster[0] : roster[game.server_idx];
}

export function currentServeSide(state: MatchState): Side | null {
  const game = currentGame(state);
  if (!game) return null;
  const score = game.score[game.serving_team];
  if (state.config.format === "singles" || state.config.scoring === "rally") {
    return score % 2 === 0 ? "R" : "L";
  }
  // Doubles side-out: the server serves from wherever they stand. Partners swap
  // on each point they score, so `pos` is the authority — not score parity.
  return game.server_idx === game.pos[game.serving_team][0] ? "R" : "L";
}

export function scoreCall(state: MatchState): string | null {
  const game = currentGame(state);
  if (!game) return null;
  const serving = game.serving_team;
  const base = `${game.score[serving]}-${game.score[other(serving)]}`;
  if (state.config.format === "doubles" && state.config.scoring === "sideout") {
    return `${base}-${game.server_num}`;
  }
  return base;
}

function isDecidingGame(state: MatchState): boolean {
  const need = gamesNeeded(state.config.best_of);
  return state.games_won.A === need - 1 && state.games_won.B === need - 1;
}

function maybeSwitchEnds(state: MatchState, game: GameState): void {
  if (game.switched_at_midpoint) return;
  const rule = state.config.switch_ends;
  if (rule === "never") return;
  if (rule === "deciding_game" && !isDecidingGame(state)) return;
  if (Math.max(game.score.A, game.score.B) < midpoint(game.target)) return;
  game.switched_at_midpoint = true;
  game.ends_swapped = !game.ends_swapped;
}

function gameFirstServer(state: MatchState, game: GameState): Team {
  if (game.number === 1) return state.config.first_server;
  let server: Team = state.config.first_server;
  for (let n = 2; n <= game.number; n += 1) {
    const prev = state.games[n - 2];
    server = nextGameFirstServer(
      state.config.first_server_rule,
      server,
      prev.winner,
    );
  }
  return server;
}

function completeGame(state: MatchState, game: GameState, winner: Team): void {
  game.status = "complete";
  game.winner = winner;
  state.games_won[winner] += 1;

  if (state.games_won[winner] >= gamesNeeded(state.config.best_of)) {
    state.status = "complete";
    state.winner = winner;
    return;
  }
  const next = nextGameFirstServer(
    state.config.first_server_rule,
    gameFirstServer(state, game),
    winner,
  );
  state.games.push(newGame(state.config, game.number + 1, next));
}

function awardPoint(
  state: MatchState,
  game: GameState,
  team: Team,
  byServer: boolean,
): void {
  game.score[team] += 1;
  if (byServer) {
    const server = currentServer(state);
    if (server) {
      state.serve_points[server.id] = (state.serve_points[server.id] ?? 0) + 1;
      state.serve_names[server.id] = server.name;
    }
  }
  if (state.config.format === "doubles") {
    const [right, left] = game.pos[team];
    game.pos[team] = [left, right];
  }
}

function applyRallyWon(state: MatchState, game: GameState): void {
  const team = game.serving_team;
  awardPoint(state, game, team, true);
  maybeSwitchEnds(state, game);
  if (
    isGameOver(game.score[team], game.score[other(team)], game.target,
      state.config.win_by_2)
  ) {
    completeGame(state, game, team);
  }
}

function applyRallyLost(state: MatchState, game: GameState): void {
  const serving = game.serving_team;
  const receiving = other(serving);

  if (state.config.scoring === "rally") {
    if (!isFrozen(game.score[receiving], state.config.freeze_at)) {
      awardPoint(state, game, receiving, false);
    }
    game.serving_team = receiving;
    game.server_num = 1;
    if (state.config.format === "doubles") {
      const even = game.score[receiving] % 2 === 0;
      game.server_idx = game.pos[receiving][even ? 0 : 1];
    } else {
      game.server_idx = 0;
    }
    maybeSwitchEnds(state, game);
    if (
      isGameOver(game.score[receiving], game.score[serving], game.target,
        state.config.win_by_2)
    ) {
      completeGame(state, game, receiving);
    }
    return;
  }

  if (state.config.format === "singles") {
    game.serving_team = receiving;
    game.server_idx = 0;
    game.server_num = 1;
    return;
  }

  if (game.server_num === 1) {
    game.server_num = 2;
    game.server_idx = 1 - game.server_idx;
    return;
  }

  // Side out. The player in the right court serves first (Rule 4.B.6).
  game.serving_team = receiving;
  game.server_num = 1;
  game.server_idx = game.pos[receiving][0];
}

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

function requireTeam(event: ScoreEvent): Team {
  if (!event.team) throw new InvalidEvent(`${event.type} requires a team`);
  return event.team;
}

export function applyEvent(state: MatchState, event: ScoreEvent): MatchState {
  if (event.type === EventType.UNDO) {
    throw new InvalidEvent("UNDO must be resolved by resolveUndos before folding");
  }

  const next = clone(state);
  if (next.status !== "live") {
    throw new InvalidEvent(`match is ${next.status}; no further events accepted`);
  }

  if (event.type === EventType.END_EARLY) {
    next.status = "abandoned";
    next.ended_early = true;
    next.games.forEach((g) => {
      if (g.status === "live") g.status = "abandoned";
    });
    return next;
  }

  if (event.type === EventType.FORFEIT) {
    const team = requireTeam(event);
    next.status = "complete";
    next.winner = other(team);
    next.forfeited_by = team;
    next.games.forEach((g) => {
      if (g.status === "live") g.status = "abandoned";
    });
    return next;
  }

  const game = currentGame(next);
  if (!game) throw new InvalidEvent("no live game");

  switch (event.type) {
    case EventType.RALLY_WON:
      applyRallyWon(next, game);
      break;
    case EventType.RALLY_LOST:
      applyRallyLost(next, game);
      break;
    case EventType.TIMEOUT: {
      const team = requireTeam(event);
      if (game.timeouts_used[team] >= next.config.timeouts_per_game) {
        throw new InvalidEvent(
          `team ${team} has used all ${next.config.timeouts_per_game} timeouts`,
        );
      }
      game.timeouts_used[team] += 1;
      break;
    }
    case EventType.TECHNICAL_WARNING: {
      game.technicals[requireTeam(event)] += 1;
      break;
    }
    case EventType.SET_FIRST_SERVER: {
      const team = requireTeam(event);
      if (game.score.A !== 0 || game.score.B !== 0) {
        throw new InvalidEvent("first server can only be set before the first rally");
      }
      game.serving_team = team;
      game.server_idx = 0;
      game.server_num =
        next.config.format === "doubles" && next.config.scoring === "sideout" ? 2 : 1;
      break;
    }
    default:
      throw new InvalidEvent(`unhandled event type ${event.type}`);
  }

  return next;
}

export function fold(
  config: MatchConfig,
  teams: Record<Team, TeamRoster>,
  events: ScoreEvent[],
): MatchState {
  let state = newMatch(config, teams);
  for (const event of resolveUndos(events)) {
    state = applyEvent(state, event);
  }
  return state;
}
