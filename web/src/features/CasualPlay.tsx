/**
 * Pickup games — the prototype's Play tab, restored.
 *
 * `PlayerPicker`, `TeamSlots` and `CoinToss` are near-verbatim ports of
 * kitchen-pass.jsx:427–530; their CSS survived the rewrite untouched, so the
 * design here is the original one. What is new is the recent-guests strip and
 * the fact that starting a game creates a real match on the server, which is
 * why the scoring screen, the offline queue and undo all work for it unchanged.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Coins, Play, Plus, Trash2, X, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  AvatarChip, Ball, Empty, ErrorNote, PALETTE, Seg, Sheet, Spinner, Toggle, cx,
} from "../components/ui";
import { api, type CasualMatch, type CasualSlot, type Player } from "../lib/api";

const TARGETS = [11, 15, 21];

/** A slot is either a saved player or a typed name with no id yet. */
type Slot = { player: Player } | { name: string };

const slotName = (slot: Slot): string =>
  "player" in slot ? slot.player.name : slot.name;

const slotToSpec = (slot: Slot): CasualSlot =>
  "player" in slot ? { player_id: slot.player.id } : { name: slot.name };

const teamLabel = (slots: (Slot | null)[], fallback: string): string => {
  const names = slots.filter(Boolean).map((s) => slotName(s as Slot).split(" ")[0]);
  return names.length ? names.join(" & ") : fallback;
};

export default function CasualPlay() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [format, setFormat] = useState<"doubles" | "singles">("doubles");
  const [scoring, setScoring] = useState<"sideout" | "rally">("sideout");
  const [target, setTarget] = useState(11);
  const [bestOf, setBestOf] = useState<1 | 3 | 5>(1);
  const [winBy2, setWinBy2] = useState(true);
  const [firstServer, setFirstServer] = useState<"A" | "B">("A");
  const [a, setA] = useState<(Slot | null)[]>([null, null]);
  const [b, setB] = useState<(Slot | null)[]>([null, null]);

  const count = format === "doubles" ? 2 : 1;
  const aReady = a.slice(0, count).every(Boolean);
  const bReady = b.slice(0, count).every(Boolean);

  // Saved players already on either side. The server refuses a player who
  // appears on both teams, so the picker should not offer them at all — only
  // ids can clash, because two typed names are two different people.
  const picked = new Set(
    [...a, ...b].flatMap((s) => (s && "player" in s ? [s.player.id] : [])),
  );

  const start = useMutation({
    mutationFn: () =>
      api.createCasualMatch({
        format,
        scoring,
        target,
        best_of: bestOf,
        win_by_2: winBy2,
        first_server: firstServer,
        a: { players: a.slice(0, count).map((s) => slotToSpec(s as Slot)) },
        b: { players: b.slice(0, count).map((s) => slotToSpec(s as Slot)) },
      }),
    onSuccess: (match) => {
      void qc.invalidateQueries({ queryKey: ["casual"] });
      // Guests created just now should show up in the picker next time.
      void qc.invalidateQueries({ queryKey: ["guests"] });
      navigate(`/matches/${match.match_id}`);
    },
  });

  return (
    <div className="stack">
      <p className="section-lede">
        Four people, one court, no tournament. Pick or type names, flip for
        serve, and score it with the same rules engine an event uses.
      </p>

      <div className="card">
        <div className="tiny-label">Format</div>
        <Seg
          options={[
            { value: "doubles", label: "Doubles" },
            { value: "singles", label: "Singles" },
          ]}
          value={format}
          onChange={(value) => setFormat(value as "doubles" | "singles")}
        />
      </div>

      <TeamSlots teamKey="A" label={teamLabel(a, "Team A")} count={count}
        values={a} onChange={setA} taken={picked} />
      <TeamSlots teamKey="B" label={teamLabel(b, "Team B")} count={count}
        values={b} onChange={setB} taken={picked} />

      <CoinToss firstServer={firstServer} onSet={setFirstServer}
        teamAName={teamLabel(a, "Team A")} teamBName={teamLabel(b, "Team B")} />

      <div className="card">
        <div className="row-between">
          <span className="tiny-label">Scoring</span>
          <Seg small
            options={[
              { value: "sideout", label: "Side-out" },
              { value: "rally", label: "Rally" },
            ]}
            value={scoring}
            onChange={(value) => setScoring(value as "sideout" | "rally")}
          />
        </div>
        <div className="divider" />
        <div className="row-between">
          <span className="tiny-label">Play to</span>
          <Seg small options={TARGETS.map((t) => ({ value: t, label: String(t) }))}
            value={target} onChange={setTarget} />
        </div>
        <div className="divider" />
        <div className="row-between">
          <span className="tiny-label">Match</span>
          <Seg small
            options={[
              { value: 1, label: "1 game" },
              { value: 3, label: "Best of 3" },
              { value: 5, label: "Best of 5" },
            ]}
            value={bestOf}
            onChange={(value) => setBestOf(value as 1 | 3 | 5)}
          />
        </div>
        <div className="divider" />
        <Toggle on={winBy2} onChange={setWinBy2} label="Win by 2"
          hint="The game continues past the target until the lead is 2" />
      </div>

      <ErrorNote error={start.error} />
      <button className="btn btn-primary btn-block btn-lg"
        disabled={!aReady || !bReady || start.isPending}
        onClick={() => start.mutate()}>
        <Play size={18} /> {start.isPending ? "Starting…" : "Start game"}
      </button>
      {(!aReady || !bReady) && (
        <p className="foot-note">
          Pick {count} player{count > 1 ? "s" : ""} for each side to start.
        </p>
      )}

      <RecentGames />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Picking players                                                      */
/* ------------------------------------------------------------------ */

function PlayerPicker({
  open, onClose, taken, onPick,
}: {
  open: boolean;
  onClose: () => void;
  taken: Set<string>;
  onPick: (slot: Slot) => void;
}) {
  const [query, setQuery] = useState("");
  const roster = useQuery({
    queryKey: ["players"], queryFn: () => api.players(), enabled: open,
  });
  const guests = useQuery({
    queryKey: ["guests"], queryFn: () => api.players(true), enabled: open,
  });

  // Clear between openings, or the next slot starts pre-filtered by whatever
  // was typed for the last one.
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const add = () => {
    const trimmed = query.trim();
    if (!trimmed) return;
    onPick({ name: trimmed });
    setQuery("");
    onClose();
  };

  // Filtering is client-side against the already-loaded roster: it is instant,
  // needs no debounce, and keeps working with no signal. `GET /players` does
  // take a `search` parameter, but a round trip per keystroke would be worse
  // for a roster this size.
  const needle = query.trim().toLowerCase();
  const matches = (p: Player) => !needle || p.name.toLowerCase().includes(needle);

  const saved = (roster.data ?? []).filter((p) => !taken.has(p.id) && matches(p));
  const recentGuests = (guests.data ?? [])
    .filter((p) => p.is_guest && !taken.has(p.id) && matches(p))
    .slice(0, 12);
  const nothingMatches = needle !== "" && saved.length === 0 && recentGuests.length === 0;

  return (
    <Sheet open={open} onClose={onClose} title="Choose a player">
      {/* Above the results, not below them: this box is a filter first and an
          add-a-one-off second, and a filter belongs where you can see what it
          is filtering. */}
      <div className="pick-adhoc">
        <input
          className="input"
          placeholder="Search, or type a new name"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <button className="btn btn-primary btn-sm" disabled={!query.trim()}
          onClick={add}>Add</button>
      </div>

      {saved.length > 0 && (
        <div className="pick-grid" style={{ marginTop: 12 }}>
          {saved.map((p) => (
            <button key={p.id} className="pick-cell"
              onClick={() => { onPick({ player: p }); onClose(); }}>
              <AvatarChip player={p} size={46} />
              <span>{p.name}</span>
            </button>
          ))}
        </div>
      )}

      {recentGuests.length > 0 && (
        <>
          <div className="tiny-label" style={{ marginTop: 12 }}>Recent guests</div>
          <p className="foot-note" style={{ margin: "2px 0 8px" }}>
            Tap the same person again rather than retyping — that is what keeps
            two players called the same name apart.
          </p>
          <div className="guest-row">
            {recentGuests.map((p) => (
              <button key={p.id} className="guest-chip"
                onClick={() => { onPick({ player: p }); onClose(); }}>
                <AvatarChip player={p} size={22} />
                <span>{p.name}</span>
              </button>
            ))}
          </div>
        </>
      )}

      {nothingMatches && (
        <p className="foot-note" style={{ marginTop: 12 }}>
          Nobody matches “{query.trim()}”. Tap <b>Add</b> to play with them as a
          one-off guest.
        </p>
      )}
    </Sheet>
  );
}

function TeamSlots({
  teamKey, label, count, values, onChange, taken,
}: {
  teamKey: "A" | "B";
  label: string;
  count: number;
  values: (Slot | null)[];
  onChange: (values: (Slot | null)[]) => void;
  /** Saved players already picked on either team. */
  taken: Set<string>;
}) {
  const [picking, setPicking] = useState<number | null>(null);
  const set = (index: number, value: Slot | null) => {
    const copy = values.slice();
    copy[index] = value;
    onChange(copy);
  };

  return (
    <div className="team-card">
      <div className="team-card-head">
        <span className="team-dot" data-team={teamKey} />
        {label}
      </div>
      <div className="slots">
        {Array.from({ length: count }).map((_, i) => {
          const slot = values[i];
          return slot ? (
            <div key={i} className="slot slot-filled">
              <AvatarChip
                player={"player" in slot ? slot.player : {
                  name: slot.name,
                  avatar: { type: "initials", color: PALETTE[slot.name.length % PALETTE.length] },
                }}
                size={34}
              />
              <span className="slot-name">{slotName(slot)}</span>
              <button className="icon-btn sm" aria-label={`Remove ${slotName(slot)}`}
                onClick={() => set(i, null)}>
                <X size={16} />
              </button>
            </div>
          ) : (
            <button key={i} className="slot slot-add" onClick={() => setPicking(i)}>
              <Plus size={17} /> Add player
            </button>
          );
        })}
      </div>
      <PlayerPicker
        open={picking !== null}
        onClose={() => setPicking(null)}
        taken={taken}
        onPick={(slot) => picking !== null && set(picking, slot)}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Coin toss                                                            */
/* ------------------------------------------------------------------ */

function CoinToss({
  firstServer, teamAName, teamBName, onSet,
}: {
  firstServer: "A" | "B";
  teamAName: string;
  teamBName: string;
  onSet: (team: "A" | "B") => void;
}) {
  const [flipping, setFlipping] = useState(false);
  const [winner, setWinner] = useState<"A" | "B" | null>(null);

  const flip = () => {
    setFlipping(true);
    setWinner(null);
    setTimeout(() => {
      setWinner(Math.random() < 0.5 ? "A" : "B");
      setFlipping(false);
    }, 900);
  };

  const wName = winner === "A" ? teamAName : teamBName;
  const lName = winner === "A" ? teamBName : teamAName;
  const loser: "A" | "B" = winner === "A" ? "B" : "A";

  return (
    <div className="card">
      <div className="card-title"><Coins size={17} /> Coin toss</div>
      <p className="card-sub">
        Flip to decide who serves first — the winner chooses to serve or to pick
        a side.
      </p>
      <div className="toss-stage">
        <div className={cx("coin", flipping && "coin-flip")}><Ball size={62} glow /></div>
      </div>
      <button className="btn btn-line btn-block" onClick={flip} disabled={flipping}>
        {flipping ? "Flipping…" : winner ? "Redo toss" : "🪙 Flip coin"}
      </button>
      {winner && !flipping && (
        <div className="toss-result">
          <p><b>{wName}</b> won the toss.</p>
          <div className="toss-choices">
            <button className="btn btn-primary btn-sm" onClick={() => onSet(winner)}>
              {wName} serves first
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => onSet(loser)}>
              Pick a side → {lName} serves
            </button>
          </div>
        </div>
      )}
      <div className="firstserve">
        <span className="tiny-label">Serving first</span>
        <Seg small
          options={[{ value: "A", label: teamAName }, { value: "B", label: teamBName }]}
          value={firstServer}
          onChange={(value) => onSet(value as "A" | "B")}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* History                                                              */
/* ------------------------------------------------------------------ */

function RecentGames() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const list = useQuery({ queryKey: ["casual"], queryFn: api.casualMatches });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteCasualMatch(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["casual"] });
      void qc.invalidateQueries({ queryKey: ["guests"] });
    },
  });

  if (list.isLoading) return <Spinner label="Loading games…" />;
  const games = list.data ?? [];

  return (
    <>
      <div className="tiny-label" style={{ marginTop: 10 }}>Recent pickup games</div>
      {games.length === 0 ? (
        <Empty icon={<Zap size={26} />} title="No pickup games yet"
          sub="Start one above and it will be kept here." />
      ) : (
        games.map((game) => (
          <PickupRow key={game.match_id} game={game}
            onOpen={() => navigate(`/matches/${game.match_id}`)}
            onDelete={() => remove.mutate(game.match_id)} />
        ))
      )}
      <ErrorNote error={remove.error} />
    </>
  );
}

function PickupRow({
  game, onOpen, onDelete,
}: {
  game: CasualMatch;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const done = game.status === "complete" || game.status === "abandoned";
  const scores = game.games.map((g) => `${g.a}–${g.b}`).join(", ");

  return (
    <div className={cx("sched-row", done && "done")}>
      <button className="sched-main pickup-main" onClick={onOpen}>
        <span className="sched-teams">
          <b className={cx(game.winner === "A" && "pickup-won")}>{game.a_name}</b>
          <em>vs</em>
          <b className={cx(game.winner === "B" && "pickup-won")}>{game.b_name}</b>
        </span>
        <span className="sched-fmt">
          {scores || "not started"}
          {" · "}
          {game.format}
          {game.scoring === "rally" ? " · rally" : ""}
          {game.best_of > 1 ? ` · best of ${game.best_of}` : ""}
        </span>
      </button>
      {!done && (
        <button className="btn btn-primary btn-sm" onClick={onOpen}>
          <Play size={14} /> {game.status === "live" ? "Resume" : "Score"}
        </button>
      )}
      <button className="icon-btn" aria-label="Delete game" onClick={onDelete}>
        <Trash2 size={16} />
      </button>
    </div>
  );
}
