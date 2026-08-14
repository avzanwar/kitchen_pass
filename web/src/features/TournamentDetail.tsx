import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronLeft, ChevronRight, LayoutGrid, Plus, Share2, Trash2, Upload, Wand2,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Empty, ErrorNote, Seg, Sheet, Spinner, cx } from "../components/ui";
import { api } from "../lib/api";

type Tab = "divisions" | "courts" | "board";

const DRAW_KINDS = [
  { value: "round_robin", label: "Round robin" },
  { value: "single_elimination", label: "Single elim" },
  { value: "double_elimination", label: "Double elim" },
  { value: "pool_playoff", label: "Pools → playoff" },
];

export default function TournamentDetail() {
  const { tournamentId = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("divisions");

  const tournament = useQuery({
    queryKey: ["tournament", tournamentId],
    queryFn: () => api.tournament(tournamentId),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteTournament(tournamentId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tournaments"] });
      navigate("/");
    },
  });

  if (tournament.isLoading) return <Spinner />;
  if (!tournament.data) return <ErrorNote error={tournament.error ?? "Not found"} />;

  const t = tournament.data;
  const publicUrl = `${window.location.origin}/live/${t.public_token}`;

  return (
    <div className="stack">
      <div className="detail-head">
        <button className="icon-btn" onClick={() => navigate("/")} aria-label="Back">
          <ChevronLeft size={20} />
        </button>
        <div className="detail-title">
          <h2>{t.name}</h2>
          <span>
            {t.status}
            {t.starts_on ? ` · ${t.starts_on}` : ""}
          </span>
        </div>
        <button className="icon-btn" onClick={() => remove.mutate()} aria-label="Delete">
          <Trash2 size={17} />
        </button>
      </div>

      <a className="share-chip" href={publicUrl} target="_blank" rel="noreferrer">
        <Share2 size={15} />
        <span>Public live scoreboard</span>
        <ChevronRight size={15} />
      </a>

      <div className="subtabs">
        {([["divisions", "Divisions"], ["courts", "Courts"], ["board", "Court board"]] as
          [Tab, string][]).map(([id, label]) => (
          <button key={id} className={cx("subtab", tab === id && "on")}
            onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>

      {tab === "divisions" && <Divisions tournamentId={tournamentId} />}
      {tab === "courts" && <Courts tournamentId={tournamentId} />}
      {tab === "board" && <CourtBoard tournamentId={tournamentId} />}
    </div>
  );
}

function Divisions({ tournamentId }: { tournamentId: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [format, setFormat] = useState("doubles");
  const [drawKind, setDrawKind] = useState("round_robin");
  const [poolCount, setPoolCount] = useState(2);
  const [bestOf, setBestOf] = useState(3);

  const list = useQuery({
    queryKey: ["divisions", tournamentId],
    queryFn: () => api.divisions(tournamentId),
  });

  const create = useMutation({
    mutationFn: () => api.createDivision(tournamentId, {
      name,
      format,
      draw_kind: drawKind,
      draw_config: drawKind === "pool_playoff"
        ? { pool_count: poolCount, advance_per_pool: 2 }
        : drawKind === "round_robin" ? { pool_count: 1 } : {},
      match_config: {
        format: format === "singles" ? "singles" : "doubles",
        best_of: bestOf,
        games_to: bestOf === 1 ? [11] : [11, 11, 15],
        win_by_2: true,
        switch_ends: "deciding_game",
      },
    }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["divisions", tournamentId] });
      setAdding(false);
      setName("");
    },
  });

  if (list.isLoading) return <Spinner />;

  return (
    <>
      {list.data?.length === 0 ? (
        <Empty icon={<LayoutGrid size={26} />} title="No divisions yet"
          sub="A division is one event — a skill level and format with its own draw." />
      ) : (
        list.data?.map((d) => (
          <Link className="tourney-row" key={d.id} to={`/divisions/${d.id}`}>
            <div className="tourney-main">
              <span className="tourney-name">{d.name}</span>
              <span className="tourney-meta">
                {d.format} · {d.draw_kind.replace(/_/g, " ")}
                {d.draw_generated ? " · draw ready" : " · no draw yet"}
              </span>
            </div>
            <ChevronRight size={18} />
          </Link>
        ))
      )}

      <button className="btn btn-primary btn-block" onClick={() => setAdding(true)}>
        <Plus size={17} /> New division
      </button>
      <button className="btn btn-line btn-block"
        onClick={() => navigate(`/import?tournament=${tournamentId}`)}>
        <Upload size={16} /> Upload divisions & teams
      </button>

      <Sheet open={adding} onClose={() => setAdding(false)} title="New division">
        <input className="input" placeholder="e.g. 4.0 Mixed Doubles" value={name}
          autoFocus onChange={(e) => setName(e.target.value)} />

        <div className="tiny-label" style={{ marginTop: 12 }}>Format</div>
        <Seg options={[
          { value: "doubles", label: "Doubles" },
          { value: "singles", label: "Singles" },
          { value: "mixed", label: "Mixed" },
        ]} value={format} onChange={setFormat} />

        <div className="tiny-label" style={{ marginTop: 12 }}>Draw</div>
        <div className="draw-grid">
          {DRAW_KINDS.map((k) => (
            <button key={k.value}
              className={cx("draw-cell", drawKind === k.value && "on")}
              onClick={() => setDrawKind(k.value)}>
              {k.label}
            </button>
          ))}
        </div>

        {drawKind === "pool_playoff" && (
          <div className="row-between" style={{ marginTop: 12 }}>
            <span className="tiny-label">Pools</span>
            <Seg small options={[2, 3, 4].map((n) => ({ value: n, label: String(n) }))}
              value={poolCount} onChange={setPoolCount} />
          </div>
        )}

        <div className="row-between" style={{ marginTop: 12 }}>
          <span className="tiny-label">Match length</span>
          <Seg small options={[
            { value: 1, label: "1 game" },
            { value: 3, label: "Best of 3" },
            { value: 5, label: "Best of 5" },
          ]} value={bestOf} onChange={setBestOf} />
        </div>

        <ErrorNote error={create.error} />
        <div className="sheet-btns">
          <button className="btn btn-ghost" onClick={() => setAdding(false)}>Cancel</button>
          <button className="btn btn-primary" disabled={!name.trim() || create.isPending}
            onClick={() => create.mutate()}>Create</button>
        </div>
      </Sheet>
    </>
  );
}

function Courts({ tournamentId }: { tournamentId: string }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const list = useQuery({
    queryKey: ["courts", tournamentId],
    queryFn: () => api.courts(tournamentId),
  });
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["courts", tournamentId] });

  const create = useMutation({
    mutationFn: () => api.createCourt(tournamentId, name.trim(), (list.data?.length ?? 0) + 1),
    onSuccess: () => { invalidate(); setName(""); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteCourt(id),
    onSuccess: invalidate,
  });

  if (list.isLoading) return <Spinner />;

  return (
    <>
      <p className="section-lede">
        Courts are what the scheduler packs matches onto. Add one row per playing court.
      </p>
      {list.data?.map((c) => (
        <div className="player-row" key={c.id}>
          <div className="team-badge">{c.name.replace(/\D/g, "") || c.name.slice(0, 1)}</div>
          <span className="player-row-name">{c.name}</span>
          <button className="icon-btn" onClick={() => remove.mutate(c.id)} aria-label="Delete">
            <Trash2 size={16} />
          </button>
        </div>
      ))}
      <ErrorNote error={create.error} />
      <ErrorNote error={remove.error} />
      <div className="pick-adhoc">
        <input className="input" placeholder="Court name" value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && name.trim() && create.mutate()} />
        <button className="btn btn-primary btn-sm" disabled={!name.trim()}
          onClick={() => create.mutate()}>Add</button>
      </div>
    </>
  );
}

function CourtBoard({ tournamentId }: { tournamentId: string }) {
  const qc = useQueryClient();
  const board = useQuery({
    queryKey: ["board", tournamentId],
    queryFn: () => api.board(tournamentId),
    refetchInterval: 10_000,
  });
  const assign = useMutation({
    mutationFn: () => api.assignCourts(tournamentId, { dry_run: false }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["board", tournamentId] }),
  });

  if (board.isLoading) return <Spinner />;
  const data = board.data;

  return (
    <>
      <button className="btn btn-primary btn-block" disabled={assign.isPending}
        onClick={() => assign.mutate()}>
        <Wand2 size={17} /> Auto-assign courts
      </button>
      <ErrorNote error={assign.error} />
      {assign.data && (
        <p className="foot-note">
          Packed into {assign.data.waves} wave{assign.data.waves === 1 ? "" : "s"}
          {assign.data.unplaced.length > 0
            ? ` · ${assign.data.unplaced.length} could not be placed`
            : " · every match placed"}
        </p>
      )}

      {data?.courts.map((c) => (
        <div className="court-card" key={c.id}>
          <div className="court-card-head">{c.name}</div>
          {c.matches.length === 0
            ? <p className="foot-note">Free</p>
            : c.matches.map((m) => (
              <Link className="court-match" key={m.match_id} to={`/matches/${m.match_id}`}>
                <span className="court-match-teams">{m.a} <em>vs</em> {m.b}</span>
                <span className="court-match-meta">{m.division_name} · {m.status}</span>
              </Link>
            ))}
        </div>
      ))}

      {data && data.queue.length > 0 && (
        <>
          <div className="tiny-label" style={{ marginTop: 12 }}>
            Waiting ({data.queue.length})
          </div>
          {data.queue.slice(0, 12).map((m) => (
            <div className="sched-row" key={m.match_id}>
              <div className="sched-main">
                <span className="sched-teams">{m.a} <em>vs</em> {m.b}</span>
                <span className="sched-fmt">{m.division_name}</span>
              </div>
            </div>
          ))}
        </>
      )}

      {data && data.conflicts.length > 0 && (
        <p className="foot-note">
          {data.conflicts.length} player{data.conflicts.length === 1 ? " is" : "s are"} in
          more than one ready match, so those cannot run at the same time.
        </p>
      )}
    </>
  );
}
