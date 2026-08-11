# Kitchen Pass — Pickleball Tournament Manager

A multi-court, multi-user pickleball tournament manager with offline-capable
scoring, grown out of the `kitchen-pass.jsx` scorekeeper prototype.

## Run it

```bash
./dev.sh --seed
```

Then open **http://localhost:5173** and sign in as
`organizer@kitchenpass.dev` / `seed-password-123`.

The seed builds a real event: 24 players, six courts, and three divisions —
pool→playoff, round robin and double elimination — with every draw generated and
45 matches ready to score. Player rosters deliberately overlap between divisions,
which is both realistic and what makes the court scheduler's conflict detection
do something visible.

Drop `--seed` on later runs to keep your data. `./dev.sh` starts the API on
:8000 (docs at `/docs`) and the web app on :5173.

### With Docker instead

```bash
cp .env.example .env
# set KP_SECRET_KEY — openssl rand -hex 32
docker compose up --build
```

Postgres, Redis, the API (behind Alembic migrations) and an nginx-served build
of the frontend, on http://localhost:8080.

## Deploying it

`Dockerfile` at the repo root builds **one image that serves both the API and
the frontend** — one service, one URL, no CORS, same-origin WebSockets. That is
what makes a free tier workable. The container migrates itself on startup, so
there is no release phase to configure.

### Neon for the database, Render for the app (both free)

Render's own free Postgres is deleted after 30 days, so the database lives on
Neon instead, whose free tier persists.

**1. Neon.** Sign up at [neon.tech](https://neon.tech), create a project, and
copy the **pooled** connection string from the dashboard. It looks like:

```
postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require
```

Take the *pooled* one (the host contains `-pooler`). Neon closes idle direct
connections, and a free web service that sleeps and wakes will otherwise trip
over dead connections in the pool.

**2. Render.** Dashboard → **New → Blueprint** → pick this repo. `render.yaml`
asks for one value, `KP_DATABASE_URL` — paste the Neon string unedited. Render
generates `KP_SECRET_KEY` itself and turns on first-boot seeding.

Paste it *unedited*: the app rewrites `postgresql://` to the async driver and
translates `sslmode`/`channel_binding` into asyncpg's `ssl`, because asyncpg
rejects the libpq spellings with `connect() got an unexpected keyword argument
'sslmode'`. Alembic gets the reverse translation, since psycopg wants `sslmode`
back. `tests/test_config.py` pins every one of those forms.

The remaining free-tier limit is not a bug: the service **sleeps after 15
minutes idle**, and the next request takes 30–60 seconds to wake it. Worth
warning testers about, or they will assume it is broken.

### Anywhere else that runs a container

Fly.io, Koyeb, Railway and Hugging Face Spaces all take the same image. Set:

| Variable | Value |
|---|---|
| `KP_SECRET_KEY` | `openssl rand -hex 32` — **required**, the app refuses to boot without it |
| `KP_DATABASE_URL` | The platform's Postgres URL, pasted unedited |
| `KP_SEED_ON_START` | `true` for a demo instance, `false`/unset for real use |

`KP_DATABASE_URL` accepts `postgres://`, `postgresql://` or
`postgresql+asyncpg://` — hosts hand out all three and the app normalises them,
because the bare forms otherwise fail at startup looking for psycopg2.

**PythonAnywhere's free tier will not work.** It serves WSGI only; FastAPI is
ASGI, and the live scoreboard needs WebSockets, which that tier cannot do at
all.

### Seeding is first-boot only

`KP_SEED_ON_START=true` loads the demo tournament **only into an empty
database**. Deployed containers restart constantly — redeploys, idle wakes,
crash loops — and re-seeding on any of those would destroy whatever testers had
entered. Verified: adding a user, restarting with seeding still enabled, and
confirming the user survives.

## What it does

**Organizer** — create a tournament, add courts, define divisions (skill, age,
format), register players and teams, generate a draw, auto-assign courts, watch
the board.

**Scorekeeper** — open a match on a phone and score it with rules-based side-out
or rally scoring, right down to which service court the server stands in.
Keeps working with no signal.

**Spectator** — a share link to live standings, brackets and the court board.
No account, updates itself, exports to CSV.

## Layout

```
kitchen-pass.jsx          the original prototype — kept as the design reference
dev.sh                    one command to run everything
server/
  app/scoring/            the authoritative scoring engine — pure, no I/O
  app/draws/              draw generation, advancement, standings — also pure
  app/scheduling/         court assignment and conflict detection — also pure
  app/models.py           SQLModel tables
  app/api/v1/             auth, players, tournaments, divisions, scoring, courts, public
  app/realtime/           WebSocket fan-out (in-process, or Redis)
  alembic/                migrations
  scripts/                corpus generation, seed data
web/
  src/scoring/engine.ts   TypeScript mirror of the Python engine
  src/lib/outbox.ts       IndexedDB rally queue + background flusher
  src/features/           screens
  e2e/                    Playwright suite
conformance/
  corpus.json             golden output of the Python engine
  parity_jsx.mjs          evaluates the prototype's engine for parity checking
```

## Testing

```bash
cd server && uv run pytest              # 333 tests
cd server && uv run ruff check . && uv run mypy
cd web    && npx vitest run             # 24 tests
cd web    && npx tsc --noEmit
cd web    && npm run e2e                # Playwright — needs the stack running
```

### The load-bearing test

`conformance/corpus.json` is generated from the Python engine and replayed by
**both** engines — `server/tests/test_conformance.py` and
`web/tests/conformance.test.ts`. It pins 15 scenarios across 908 step digests
plus full final-state snapshots.

If the two engines ever disagree by one rally, a scorekeeper sees a different
score from the spectators and offline reconciliation fights itself. Treat a
failure here as a release blocker.

```bash
cd server
uv run pytest                                # see what breaks
uv run python scripts/generate_corpus.py     # re-freeze after an intended change
git diff ../conformance/corpus.json          # review every changed step
```

An unreviewed diff in `corpus.json` is a behaviour regression.

There is also `test_prototype_parity.py`, which evaluates the scoring functions
*directly out of `kitchen-pass.jsx`* in Node and asserts the Python engine
reproduces them rally for rally — 188 states across both formats, deuce, and a
long random game. Nothing about the original scoring behaviour changed on the
way into Python.

## How it works

### Scoring is an event log

Match state is a **fold over an append-only log** — `RALLY_WON`, `RALLY_LOST`,
`TIMEOUT`, `TECHNICAL_WARNING`, `UNDO`, `SET_FIRST_SERVER`, `END_EARLY`,
`FORFEIT`. There is no partial-update path.

Game and match transitions are *derived* by the fold rather than recorded as
events, which is what makes undo clean: undoing the point that won game 1
deletes game 2 and rolls back the match tally in one step, with no half-applied
structural event stranded in the log.

Beyond the prototype it models matches (best-of-N with per-game targets), end
switches at the midpoint and between games, rally scoring with an optional
freeze, timeouts, forfeits, and serve-point attribution keyed by player id
rather than name.

Rules that differ between sanctioning bodies are configuration, not assumptions
— notably `first_server_rule` (who serves first in games 2 and 3) and
`switch_ends`. Set them to match your event's rulebook.

### Offline-first scoring

Every tap is written to IndexedDB **first**, folded locally by the mirrored
TypeScript engine, and pushed by a background flusher with exponential backoff.
Each event carries a client-generated `client_event_id`; the server's
`uq_event_client_id` constraint makes a retried batch idempotent, so a flush
whose response is lost simply replays and is ignored.

If the server rejects a queued batch — usually because the match was scored on
another device — the events are discarded rather than retried forever, and the
UI says so and reloads the authoritative score. Exactly one scorekeeper holds a
match at a time via a lease, which turns a distributed-merge problem into a
lock.

### Draws resolve, they don't rebuild

A draw is emitted with **unresolved slots**: a slot names an entry, is a bye, or
forward-references a result ("winner of W-R1-M3", "2nd in pool B"). Advancement
is `resolve_draw(draw, winners)` walking that graph to a fixpoint, so the
bracket shape never changes and the whole draw sheet is printable before a ball
is hit.

| Format | Entry point |
|---|---|
| Round robin (single/double, snake-seeded pools) | `round_robin_draw` |
| Single elimination (+ optional third place) | `single_elimination` |
| Double elimination (+ conditional grand-final reset) | `double_elimination` |
| Pool play → playoff | `pool_playoff_draw` |

Two subtleties that had bugs during development and now have tests:

- A bye in the winners bracket produces **no loser**, so the losers-bracket slot
  fed by it must itself become a bye. Without that, every non-power-of-two
  double-elimination field deadlocks.
- Round numbers are **per bracket**, and the losers bracket runs longer (2k-2
  rounds) than the winners bracket. The grand final has to be numbered past
  both, and "which match decides the title" is an explicit flag rather than the
  highest round number.

### Standings

A configurable tiebreaker chain, defaulting to the USA Pickleball order:
head-to-head → point differential → points allowed. Applied **recursively**, so
a three-way tie partly broken by one criterion is re-broken among whoever is
still level. Head-to-head is skipped when the tied teams haven't all played each
other rather than scored as 0-0.

Ties nothing can separate are flagged `unresolved_tie` instead of silently
ordered, and `pool_rank_map` omits them — a playoff must not be seeded off a
placement nobody has decided.

### Court scheduling

A greedy assigner packs ready matches into waves. It will not put one player on
two courts at once, respects a configurable rest gap, and schedules around
matches already in progress. When it cannot place something it reports the
reason instead of silently reordering the day.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `KP_DATABASE_URL` | `sqlite+aiosqlite:///./kitchen_pass.db` | `postgresql+asyncpg://…` in deployment |
| `KP_SECRET_KEY` | dev placeholder | **Required** in production; the app refuses to boot otherwise |
| `KP_REDIS_URL` | *(unset)* | Without it, WebSocket fan-out is in-process — correct for a single worker |
| `KP_CORS_ORIGINS` | `localhost:5173` | Comma-separated |
| `KP_DEBUG` | `false` | Creates tables on startup; skips the production-safety check |

**Database portability.** SQLAlchemy targets Postgres in deployment and SQLite
in tests, so the suite runs with no services installed. JSON columns become
JSONB on Postgres via `models.json_column()`. Postgres-specific behaviour is not
exercised until you point `KP_DATABASE_URL` at a real Postgres — add a Postgres
CI job before deploying. `tests/test_migrations.py` diffs the Alembic head
against live model metadata so the schema cannot drift unnoticed.

**Auth** is a lean JWT implementation rather than `fastapi-users`, which wants
ownership of the user model and fights SQLModel. Two token audiences: `access`
for signed-in users, and short-lived `court` tokens scoped to a single match so
a volunteer scorekeeper needs no account.

## Known gaps

- **The Playwright suite has not been executed here** — Chromium would not
  download in this environment. The specs in `web/e2e/` are written and include
  the offline/reconnect convergence test; run `npm run e2e` with the stack up.
- **Nothing has run against a real Postgres.** The URL handling for every form
  Neon and Render hand out is unit-tested, and the schema uses no
  Postgres-specific types, but the first real connection will happen on your
  deploy. If it fails, the logs will say so at startup rather than mid-event.
- Deferred by design: DUPR/Elo ratings, open-play rotation and ladders, payments
  and entry fees, PDF bracket sheets (CSV export is in), and push notifications.
