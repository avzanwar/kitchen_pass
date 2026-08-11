/**
 * Prototype parity harness.
 *
 * Loads the scoring engine *out of the original `kitchen-pass.jsx`* — by slicing
 * the pure region of the file and evaluating it — rather than a hand-copied
 * version, so this genuinely checks the prototype's behaviour and not a
 * transcription of it.
 *
 * Emits a JSON trace on stdout. `server/tests/test_prototype_parity.py` runs the
 * same sequences through the Python engine and asserts the traces match.
 *
 *   node conformance/parity_jsx.mjs > /tmp/jsx_trace.json
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const SOURCE = resolve(here, "..", "kitchen-pass.jsx");

// The pure region: `uid`/`keyOf` helpers through the end of `applyResult`.
// Everything here is a function declaration, so evaluating it touches no
// browser globals.
const FIRST_LINE = 22;
const LAST_LINE = 127;

const lines = readFileSync(SOURCE, "utf8").split("\n");
const slice = lines.slice(FIRST_LINE - 1, LAST_LINE).join("\n");

if (!/function applyResult/.test(slice) || !/function makeGame/.test(slice)) {
  throw new Error(
    `kitchen-pass.jsx lines ${FIRST_LINE}-${LAST_LINE} no longer contain the ` +
      `scoring engine — update the slice bounds in this harness.`,
  );
}

const engine = new Function(
  `${slice}\nreturn { makeGame, applyResult, serveSide, callStr, curServerPlayer, keyOf };`,
)();

const TEAMS = {
  A: { name: "Ann & Bo", players: [{ id: "a1", name: "Ann" }, { id: "a2", name: "Bo" }] },
  B: { name: "Cy & Di", players: [{ id: "b1", name: "Cy" }, { id: "b2", name: "Di" }] },
};
const SINGLES = {
  A: { name: "Ann", players: [{ id: "a1", name: "Ann" }] },
  B: { name: "Cy", players: [{ id: "b1", name: "Cy" }] },
};

function digest(g) {
  const done = g.status !== "live";
  return {
    status: done ? "complete" : "live",
    winner: g.winner ?? null,
    score: { A: g.score.A, B: g.score.B },
    serving_team: done ? null : g.servingTeam,
    server_id: done ? null : engine.curServerPlayer(g).id,
    server_num: done ? null : g.serverNum,
    side: done ? null : engine.serveSide(g),
    // The prototype renders the call with an en dash; the Python engine uses a
    // plain hyphen. Cosmetic, so normalise rather than treat it as divergence.
    call: done ? null : engine.callStr(g).replace(/–/g, "-"),
    // `servePts` is keyed by `keyOf(player)` -> "id:a1". Strip the prefix so the
    // two engines' stat maps are comparable.
    serve_points: Object.fromEntries(
      Object.entries(g.servePts).map(([k, v]) => [k.replace(/^id:/, ""), v]),
    ),
  };
}

function run(setup, results) {
  let g = engine.makeGame(setup);
  const steps = [digest(g)];
  for (const servingWon of results) {
    if (g.status !== "live") break;
    g = engine.applyResult(g, servingWon);
    steps.push(digest(g));
  }
  return steps;
}

const W = true;
const L = false;

const cases = [
  {
    name: "doubles_mixed_sequence",
    setup: {
      format: "doubles", target: 11, winBy2: true, firstServer: "A", teams: TEAMS,
    },
    results: [W, W, L, L, W, L, L, W, W, W, L, L, W, W],
  },
  {
    name: "doubles_run_to_eleven",
    setup: {
      format: "doubles", target: 11, winBy2: true, firstServer: "A", teams: TEAMS,
    },
    results: Array(11).fill(W),
  },
  {
    name: "doubles_deuce",
    setup: {
      format: "doubles", target: 11, winBy2: true, firstServer: "A", teams: TEAMS,
    },
    results: [
      ...Array(10).fill(W), L, ...Array(10).fill(W), L, L, W, L, L, W, W,
    ],
  },
  {
    name: "doubles_first_server_b",
    setup: {
      format: "doubles", target: 15, winBy2: true, firstServer: "B", teams: TEAMS,
    },
    results: [W, L, L, W, W, L, W, L, L, L, W, W, W],
  },
  {
    name: "doubles_no_win_by_two",
    setup: {
      format: "doubles", target: 11, winBy2: false, firstServer: "A", teams: TEAMS,
    },
    results: [...Array(10).fill(W), L, ...Array(10).fill(W), L, L, W],
  },
  {
    name: "singles_mixed_sequence",
    setup: {
      format: "singles", target: 11, winBy2: true, firstServer: "A", teams: SINGLES,
    },
    results: [W, W, L, W, L, L, W, W, W, L, W, W, W, W, W, W],
  },
  {
    name: "singles_first_server_b",
    setup: {
      format: "singles", target: 21, winBy2: true, firstServer: "B", teams: SINGLES,
    },
    results: [L, W, W, L, L, W, L, W, W, W, L, W],
  },
];

// A long pseudo-random sequence (deterministic LCG so both languages agree).
let seed = 20260811;
const nextBool = () => {
  seed = (seed * 1103515245 + 12345) % 2147483648;
  return seed % 3 !== 0; // ~2/3 rallies won by the server
};
cases.push({
  name: "doubles_random_long",
  setup: { format: "doubles", target: 21, winBy2: true, firstServer: "A", teams: TEAMS },
  results: Array.from({ length: 400 }, nextBool),
});

const out = cases.map((c) => ({
  name: c.name,
  setup: {
    format: c.setup.format,
    target: c.setup.target,
    winBy2: c.setup.winBy2,
    firstServer: c.setup.firstServer,
    singles: c.setup.format === "singles",
  },
  results: c.results,
  steps: run(c.setup, c.results),
}));

process.stdout.write(JSON.stringify({ version: 1, cases: out }, null, 2) + "\n");
