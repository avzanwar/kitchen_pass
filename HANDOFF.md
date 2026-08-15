# Kitchen Pass — handoff

State of the project as of **2026-08-15**. Written so a fresh session can pick it
up without re-deriving anything.

`README.md` explains *how the system works* — read it for architecture. This file
covers *where things stand*, how to operate it, and the traps that have already
bitten, which are not obvious from the code.

---

## What it is

A pickleball tournament manager, grown from `kitchen-pass.jsx` — a single-file
React prototype kept in the repo as the design reference. The prototype's
scoring engine and visual design both survived into the real app.

It does three things:

1. **Run a tournament** — divisions, teams, four draw formats, seeding, courts,
   scheduling, standings with real tiebreakers, live public scoreboard.
2. **Bulk upload** an event from a spreadsheet (`.xlsx` / `.csv`).
3. **Pickup games** — score a one-off match with no tournament at all.

---

## Where it lives

| | |
|---|---|
| Repo | `https://github.com/avzanwar/kitchen_pass` (private), branch `main` |
| Local | `/Users/avzanwar/Projects/kitchen-pass` |
| Live | `https://kitchen-pass.onrender.com` |
| Render service | `srv-d9thlkqjobas73d16lmg` (free plan, sleeps after 15 min idle, 30–60 s cold start) |
| Database | Neon Postgres, pooled connection, `KP_DATABASE_URL` set in Render |
| Demo login | `organizer@kitchenpass.dev` / `seed-password-123` |
| Public scoreboard | `/live/09ea71aa5f3b4b30ae06f62590e03c08` |

**There is a real user on production**: `sandeepkabra007@gmail.com`, with a
tournament ("Mpf pickle ball") and 6 players. Treat production data as live —
do not truncate tables, and clean up any throwaway accounts you create.

---

## Current state

Everything is committed, pushed, and deployed. Working tree clean.

```
671ee15  Let the pickup-game player picker search the roster      <- HEAD, live
426312b  Fix pickup games on Postgres: tournaments.kind must be a native enum
79c7311  Add pickup games: score a one-off match with no tournament
28406fd  Add bulk upload of divisions, teams and players from a spreadsheet
0d9943c  Fix the top bar wrapping and underlined card links
1b991d6  Fix two bugs that only real Postgres could reveal
c3c71a8  Target Neon for the database instead of Render's free Postgres
76df7fd  Make the app deployable as a single container
25b37e6  Pickleball tournament manager built on the Kitchen Pass prototype
```

**Tests: 460 backend, 56 frontend.** Ruff and `mypy --strict` both clean.

Alembic head on production: `b3d81a44c206` (matches the repo).

---

## Running it

```bash
./dev.sh --seed     # API on :8000, web on :5173, demo data loaded
```

```bash
cd server && uv run pytest && uv run ruff check . && uv run mypy
cd web    && npx vitest run && npx tsc --noEmit && npm run build
```

If `uv run pytest` fails with `Failed to spawn: pytest` or a `bad interpreter`
error, the venv has a stale absolute path (the project directory was renamed at
some point). Fix: `rm -rf server/.venv && uv sync --all-groups`.

### Deploying

Push to `main`, then trigger a deploy. **Auto-deploy on push has never actually
fired** despite the service reporting `autoDeploy: yes` — the GitHub webhook
looks unwired. Trigger manually:

```bash
KEY=$(grep -A2 "^api:" ~/.render/cli.yaml | grep "key:" | awk '{print $2}')
curl -s -X POST "https://api.render.com/v1/services/srv-d9thlkqjobas73d16lmg/deploys" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d "{\"clearCache\":\"do_not_clear\",\"commitId\":\"$(git rev-parse HEAD)\"}"
```

**Always pass `commitId`.** A bare trigger once snapshotted the *previous*
commit and deployed the wrong build. Deploys take ~1–4 minutes; migrations run
automatically in the app's lifespan on boot.

---

## The three load-bearing ideas

Detail is in `README.md`; this is the orientation.

1. **Scoring is an event log.** A match's state is `fold(events)`. That one
   decision gives undo, offline sync, live spectating and an audit trail from
   the same mechanism. The Python engine is authoritative; `web/src/scoring/
   engine.ts` mirrors it, and a **golden conformance corpus** (`conformance/
   corpus.json`) is replayed against both. If that test fails, treat it as a
   release blocker, not a flake.

2. **A pickup game is a real match in a hidden container.** One `Tournament` per
   user with `kind="casual"`, holding one `Division` per game. That is what lets
   `LiveMatch.tsx` and `useMatch.ts` serve pickup games completely unchanged —
   offline queue, undo, leases and the live feed all come free. Making
   `Match.division_id` nullable instead would have put a null branch in every
   read path that checks ownership.

3. **Bulk import previews before it writes.** `app/imports/` is pure: `sheet.py`
   absorbs file mess, `plan.py` produces a plan plus problems. The preview runs
   the identical code the commit runs, and the import is all-or-nothing.

---

## Traps that have already bitten

Read this section before changing anything schema-adjacent. Every item below
cost real debugging time.

### SQLite hides Postgres bugs — three times now

Tests run on SQLite; production is Postgres. These all passed locally and failed
in deployment:

- **Naive vs aware timestamps.** Eight columns had to become `TIMESTAMPTZ`. Use
  `utc_timestamp_column()` in `models.py` for any new datetime.
- **Missing DB-level cascades.** Deleting a tournament 500'd. SQLite ignored the
  foreign keys entirely; the test fixture now sets `PRAGMA foreign_keys=ON`.
- **Enum columns.** SQLModel maps an `Enum` field to a *native* Postgres enum, so
  queries bind `$1::thattype`. A migration that adds the column as `VARCHAR`
  passes every SQLite test and then fails with `type "..." does not exist`.
  `tests/test_migrations.py::test_every_enum_column_becomes_a_native_postgres_type`
  now guards this by rendering the migration chain to Postgres DDL via Alembic's
  offline mode — no server needed.

**Before deploying any migration**, run it against real Postgres inside a
transaction and roll back. This proves the DDL and the backfill without touching
production:

```python
import psycopg
with psycopg.connect(NEON_URL) as conn:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE ...")          # the migration's DDL
        cur.execute("SELECT col, count(*) FROM t GROUP BY col")
        print(cur.fetchall())                   # existing rows backfilled?
    conn.rollback()
```

You can also create a scratch database on Neon (`CREATE DATABASE kp_scratch`),
migrate it, exercise the feature, and drop it. Remember to drop it.

### The service worker serves a stale bundle after deploy

The PWA precaches the app shell, so a returning user keeps the old JS until the
service worker updates. After every deploy, a hard reload (Cmd+Shift+R) is
needed to see changes. If someone reports a shipped feature "missing", this is
almost always why. **There is no "update available, tap to reload" prompt yet** —
adding one is a good small task.

To confirm a deploy really shipped, grep the served bundle rather than trusting
the deploy status:

```bash
BUNDLE=$(curl -s https://kitchen-pass.onrender.com/ | grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' | head -1)
curl -s "https://kitchen-pass.onrender.com$BUNDLE" -o /tmp/live.js
grep -c "some new string" /tmp/live.js
```

### Other sharp edges

- **`models.py` must not have `from __future__ import annotations`.** It breaks
  SQLModel's `Relationship` resolution. There is a comment saying so; keep it.
- **TanStack `queryFn: api.someFn` is dangerous** when the function takes
  arguments — the query context object arrives as the first parameter. This
  silently defeated the guest filter until `tsc` caught it. Always wrap:
  `queryFn: () => api.someFn()`.
- **Response schemas use `extra="forbid"`**, so build them field by field rather
  than dumping an ORM row.
- **Alembic migrations must be `create_table`/`add_column` only.** Constraint
  `ALTER`s break SQLite, which is why the initial migration is squashed.

---

## Feature notes worth knowing

### Pickup games (`/play`)

- Typed names become **guests**: real `Player` rows with `is_guest=True`, real
  ids, hidden from the saved roster. **Every typed name creates a new row —
  names are never matched.** That is the deliberate fix for the prototype's
  `keyOf` bug (`kitchen-pass.jsx:24`) where two players called "Mike" shared one
  serve-stat bucket. Re-picking the same person is done from the recent-guests
  strip.
- "Hidden" rests on **three filters**, each with an explicit test. Breaking any
  one leaks casual data into the tournament UI:
  1. `api/v1/tournaments.py` — casual container excluded from `GET /tournaments`
  2. `api/v1/players.py` — guests excluded from `GET /players` (opt in with
     `?include_guests=true`)
  3. `services/import_service.py` — guests excluded from bulk-import name matching
- Draw resolution is skipped for casual divisions in `api/v1/scoring.py`.
- The picker searches the roster client-side (substring, case-insensitive).

### Bulk import (`/import`)

- Templates at `GET /api/v1/imports/template.{csv,xlsx}` are **deliberately
  public** — they hold no user data, and a browser download link cannot carry a
  bearer token.
- The template is generated from the same column definitions the parser accepts,
  and a test asserts the CSV and XLSX versions import identically.

---

## Open items

**Deferred by the user, not blocked:**

- **Rotate the Neon password.** It was pasted in an earlier chat and is still
  live. After rotating, update `KP_DATABASE_URL` in Render.
- **Delete the unused Render Postgres** `kitchen-pass-db` — left over from before
  the move to Neon, expires 2026-09-10. Nothing points at it.

**Never executed here:**

- **The Playwright suite** (`web/e2e/app.spec.ts`) — Chromium would not download
  in this environment. The specs are written, including offline/reconnect
  convergence. Run `npm run e2e` with the stack up.

**Good next tasks, roughly by value:**

1. A service-worker update prompt, so deploys reach users without a hard reload.
2. Wire up Render auto-deploy properly, or accept manual triggers and document it.
3. Promote a guest to a saved roster player from the UI (the data model already
   supports it — flip `is_guest`).
4. PDF bracket and score sheets (CSV export already exists).
5. Withdrawal handling in pickup games, and deleting a guest who is no longer in
   any match from the Players screen.

**Deferred by design** (noted so the model does not preclude them): DUPR/Elo
ratings and rating sync, open-play / king-of-the-court rotation and ladders,
partner-chemistry stats, payments and entry fees, live streaming overlays,
native app wrappers.

---

## How work has been verified here

Worth continuing, because it has repeatedly caught things tests did not:

1. Full suite + ruff + mypy + tsc + production build.
2. A scripted end-to-end run against a **live local stack** over HTTP, driving
   the same endpoints the browser uses.
3. **A real browser pass** on the running app. This is what caught the top bar
   wrapping, the underlined card links, and the picker offering a player already
   on the other team — none of which any test would have failed on.
4. A scripted verification against **production** after deploying, using a
   throwaway account that deletes everything it created.
