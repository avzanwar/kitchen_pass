import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Medal, Play, Plus, Shuffle, Users } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { AvatarChip, Empty, ErrorNote, Sheet, Spinner, cx } from "../components/ui";
import { api, type Player } from "../lib/api";

type Tab = "entries" | "draw" | "standings";

export default function DivisionDetail() {
  const { divisionId = "" } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("entries");

  const division = useQuery({
    queryKey: ["division", divisionId],
    queryFn: () => api.division(divisionId),
  });

  if (division.isLoading) return <Spinner />;
  if (!division.data) return <ErrorNote error={division.error ?? "Not found"} />;
  const d = division.data;

  return (
    <div className="stack">
      <div className="detail-head">
        <button className="icon-btn" onClick={() => navigate(`/tournaments/${d.tournament_id}`)}
          aria-label="Back">
          <ChevronLeft size={20} />
        </button>
        <div className="detail-title">
          <h2>{d.name}</h2>
          <span>{d.format} · {d.draw_kind.replace(/_/g, " ")}</span>
        </div>
      </div>

      <div className="subtabs">
        {([["entries", "Teams"], ["draw", "Draw"], ["standings", "Standings"]] as
          [Tab, string][]).map(([id, label]) => (
          <button key={id} className={cx("subtab", tab === id && "on")}
            onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      {tab === "entries" && <Entries divisionId={divisionId} locked={d.draw_generated}
        singles={d.format === "singles"} />}
      {tab === "draw" && <DrawView divisionId={divisionId} />}
      {tab === "standings" && <Standings divisionId={divisionId} />}
    </div>
  );
}

function Entries({ divisionId, locked, singles }: {
  divisionId: string; locked: boolean; singles: boolean;
}) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [picked, setPicked] = useState<Player[]>([]);

  const entries = useQuery({
    queryKey: ["entries", divisionId], queryFn: () => api.entries(divisionId),
  });
  const players = useQuery({ queryKey: ["players"], queryFn: () => api.players() });

  const need = singles ? 1 : 2;
  const create = useMutation({
    mutationFn: () => api.createEntry(divisionId, {
      player_ids: picked.map((p) => p.id),
      seed: (entries.data?.length ?? 0) + 1,
    }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["entries", divisionId] });
      setPicked([]); setAdding(false);
    },
  });

  const withdraw = useMutation({
    mutationFn: (id: string) => api.setEntryStatus(id, "withdrawn"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["entries", divisionId] }),
  });

  if (entries.isLoading) return <Spinner />;
  const taken = new Set(entries.data?.flatMap((e) => e.players.map((p) => p.id)) ?? []);

  return (
    <>
      {entries.data?.length === 0 ? (
        <Empty icon={<Users size={26} />} title="No teams registered"
          sub={singles ? "Add players to this event." : "Register your pairs."} />
      ) : (
        entries.data?.map((e) => (
          <div className={cx("player-row", e.status === "withdrawn" && "muted-row")} key={e.id}>
            <div className="team-badge">{e.seed ?? "–"}</div>
            <span className="player-row-name">
              {e.name}
              {e.status === "withdrawn" && <em className="rating-tag">withdrawn</em>}
            </span>
            <div className="entry-avatars">
              {e.players.map((p) => <AvatarChip key={p.id} player={p} size={26} />)}
            </div>
            {e.status !== "withdrawn" && (
              <button className="btn btn-ghost btn-sm" onClick={() => withdraw.mutate(e.id)}>
                Withdraw
              </button>
            )}
          </div>
        ))
      )}
      <ErrorNote error={withdraw.error} />

      {locked ? (
        <p className="foot-note">
          The draw is generated. Regenerate it from the Draw tab to change the field.
        </p>
      ) : (
        <button className="btn btn-primary btn-block" onClick={() => setAdding(true)}>
          <Plus size={17} /> {singles ? "Add player" : "Add pair"}
        </button>
      )}

      <Sheet open={adding} onClose={() => setAdding(false)}
        title={singles ? "Add a player" : "Add a pair"}>
        <p className="sheet-text">
          Pick {need} player{need > 1 ? "s" : ""}. Order matters: the first serves
          from the right at 0-0.
        </p>
        <div className="pick-grid">
          {players.data?.filter((p) => !taken.has(p.id)).map((p) => {
            const index = picked.findIndex((x) => x.id === p.id);
            return (
              <button key={p.id} className={cx("pick-cell", index >= 0 && "on")}
                onClick={() => setPicked((cur) =>
                  index >= 0
                    ? cur.filter((x) => x.id !== p.id)
                    : cur.length < need ? [...cur, p] : cur)}>
                <AvatarChip player={p} size={46} />
                <span>{p.name}</span>
                {index >= 0 && <b className="pick-order">{index + 1}</b>}
              </button>
            );
          })}
        </div>
        <ErrorNote error={create.error} />
        <div className="sheet-btns">
          <button className="btn btn-ghost" onClick={() => setAdding(false)}>Cancel</button>
          <button className="btn btn-primary"
            disabled={picked.length !== need || create.isPending}
            onClick={() => create.mutate()}>
            Register
          </button>
        </div>
      </Sheet>
    </>
  );
}

function DrawView({ divisionId }: { divisionId: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const draw = useQuery({
    queryKey: ["draw", divisionId], queryFn: () => api.draw(divisionId),
    refetchInterval: 15_000,
  });
  const generate = useMutation({
    mutationFn: (replace: boolean) => api.generateDraw(divisionId, replace),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["draw", divisionId] });
      void qc.invalidateQueries({ queryKey: ["division", divisionId] });
    },
  });

  if (draw.isLoading) return <Spinner />;
  const matches = draw.data?.matches ?? [];

  if (matches.length === 0) {
    return (
      <>
        <Empty icon={<Shuffle size={26} />} title="No draw yet"
          sub="Generate it once your teams are registered." />
        <ErrorNote error={generate.error} />
        <button className="btn btn-primary btn-block" disabled={generate.isPending}
          onClick={() => generate.mutate(false)}>
          <Shuffle size={17} /> Generate draw
        </button>
      </>
    );
  }

  const rounds = [...new Set(matches.map((m) => m.round))].sort((a, b) => a - b);

  return (
    <>
      {rounds.map((round) => (
        <div key={round}>
          <div className="tiny-label" style={{ marginTop: 10 }}>
            {matches.find((m) => m.round === round)?.label ?? `Round ${round}`}
          </div>
          {matches.filter((m) => m.round === round).map((m) => (
            <div className={cx("sched-row", m.status === "complete" && "done")} key={m.id}>
              <div className="sched-main">
                <span className="sched-teams">
                  {m.entry_a_name ?? m.a_label} <em>vs</em> {m.entry_b_name ?? m.b_label}
                </span>
                <span className="sched-fmt">
                  {m.pool ? `Pool ${m.pool} · ` : ""}{m.status}
                </span>
              </div>
              {m.status === "ready" || m.status === "live" ? (
                <button className="btn btn-primary btn-sm"
                  onClick={() => navigate(`/matches/${m.id}`)}>
                  <Play size={14} /> {m.status === "live" ? "Resume" : "Score"}
                </button>
              ) : null}
            </div>
          ))}
        </div>
      ))}
      <ErrorNote error={generate.error} />
      <button className="btn btn-line btn-block" disabled={generate.isPending}
        onClick={() => generate.mutate(true)}>
        <Shuffle size={16} /> Regenerate draw
      </button>
      <p className="foot-note">
        Regenerating is refused once any match has been played.
      </p>
    </>
  );
}

function Standings({ divisionId }: { divisionId: string }) {
  const standings = useQuery({
    queryKey: ["standings", divisionId], queryFn: () => api.standings(divisionId),
    refetchInterval: 15_000,
  });
  if (standings.isLoading) return <Spinner />;

  return (
    <>
      {standings.data?.map((table) => (
        <div key={table.pool ?? "all"}>
          {table.pool && <div className="tiny-label" style={{ marginTop: 10 }}>
            Pool {table.pool}
          </div>}
          <div className="table">
            <div className="table-head">
              <span>Team</span><span>W</span><span>L</span><span>+/−</span><span>Pts</span>
            </div>
            {table.rows.map((row) => (
              <div className="table-row" key={row.entry_id}>
                <span className="t-name">
                  {row.rank === 1 && row.played > 0 && <Medal size={13} className="lead-medal" />}
                  {row.entry_name}
                  {row.unresolved_tie && <em className="rating-tag" title="Needs a coin flip">tie</em>}
                </span>
                <span>{row.wins}</span>
                <span>{row.losses}</span>
                <span>{row.point_diff >= 0 ? "+" : ""}{row.point_diff}</span>
                <span className="t-pts">{row.points_for}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      <p className="foot-note">
        Ties break head-to-head, then point differential, then points allowed.
        A "tie" flag means no criterion separated them — that is a coin flip.
      </p>
    </>
  );
}
