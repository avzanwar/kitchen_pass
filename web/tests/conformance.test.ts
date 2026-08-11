/**
 * Replay the golden corpus against the TypeScript engine.
 *
 * This is the load-bearing test in the whole project. The offline client folds
 * events locally while the server folds the same events authoritatively; if the
 * two disagree by even one rally, a scorekeeper sees a different score from the
 * spectators and the sync reconciliation will fight itself.
 *
 * The corpus is generated from the Python engine
 * (`server/scripts/generate_corpus.py`). A failure here means either the mirror
 * has drifted, or Python changed and the corpus was regenerated without
 * updating this side.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  currentGame,
  currentServeSide,
  currentServer,
  fold,
  newMatch,
  scoreCall,
  type MatchConfig,
  type MatchState,
  type ScoreEvent,
  type Team,
  type TeamRoster,
} from "../src/scoring/engine";

interface Digest {
  status?: string;
  winner?: string | null;
  games_won?: Record<string, number>;
  game?: number | null;
  score?: Record<string, number> | null;
  serving_team?: string | null;
  server_id?: string | null;
  server_num?: number | null;
  side?: string | null;
  call?: string | null;
  ends_swapped?: boolean | null;
  error?: string;
}

interface Corpus {
  version: number;
  cases: {
    name: string;
    description: string;
    config: MatchConfig;
    teams: Record<Team, TeamRoster>;
    events: ScoreEvent[];
    steps: Digest[];
    final: Record<string, unknown>;
  }[];
}

const CORPUS_PATH = resolve(__dirname, "..", "..", "conformance", "corpus.json");
const corpus: Corpus = JSON.parse(readFileSync(CORPUS_PATH, "utf8"));

/** Must stay identical to `digest()` in generate_corpus.py. */
function digest(state: MatchState): Digest {
  const game = currentGame(state);
  const server = currentServer(state);
  return {
    status: state.status,
    winner: state.winner,
    games_won: { ...state.games_won },
    game: game ? game.number : null,
    score: game ? { ...game.score } : null,
    serving_team: game ? game.serving_team : null,
    server_id: server ? server.id : null,
    server_num: game ? game.server_num : null,
    side: currentServeSide(state),
    call: scoreCall(state),
    ends_swapped: game ? game.ends_swapped : null,
  };
}

describe("conformance with the Python engine", () => {
  it("loads a corpus with meaningful coverage", () => {
    expect(corpus.version).toBe(1);
    const names = corpus.cases.map((c) => c.name);
    for (const required of [
      "prototype_single_game_doubles",
      "singles_game_to_11",
      "win_by_two_deuce",
      "best_of_three_into_deciding_game",
      "undo_across_game_boundary",
      "rally_scoring_with_freeze",
      "forfeit_mid_match",
      "end_early",
    ]) {
      expect(names).toContain(required);
    }
    const steps = corpus.cases.reduce((n, c) => n + c.steps.length, 0);
    expect(steps).toBeGreaterThan(500);
  });

  for (const testCase of corpus.cases) {
    it(`${testCase.name}: ${testCase.description}`, () => {
      const { config, teams, events, steps } = testCase;

      expect(digest(newMatch(config, teams))).toEqual(steps[0]);

      const applied: ScoreEvent[] = [];
      events.forEach((event, i) => {
        const expected = steps[i + 1];
        applied.push(event);

        let state: MatchState;
        try {
          state = fold(config, teams, applied);
        } catch (error) {
          expect(
            expected.error,
            `step ${i + 1}: TS rejected ${event.type} but the corpus expects ` +
              `it to succeed (${(error as Error).message})`,
          ).toBeDefined();
          expect((error as Error).message).toBe(expected.error);
          applied.pop();
          return;
        }

        expect(
          expected.error,
          `step ${i + 1}: TS accepted ${event.type} but the corpus expects ` +
            `it to be rejected`,
        ).toBeUndefined();
        expect(digest(state), `diverged at step ${i + 1} (${event.type})`).toEqual(
          expected,
        );
      });

      // Whole-state check, not just the per-step digest.
      const finalState = fold(config, teams, applied);
      expect(JSON.parse(JSON.stringify(finalState))).toEqual(testCase.final);
    });
  }
});
