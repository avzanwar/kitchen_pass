import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ChevronRight, Plus, Trophy, Upload } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Empty, ErrorNote, Sheet, Spinner } from "../components/ui";
import { api } from "../lib/api";

export default function Tournaments() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const list = useQuery({ queryKey: ["tournaments"], queryFn: api.tournaments });
  const create = useMutation({
    mutationFn: () => api.createTournament({
      name, starts_on: from || undefined, ends_on: to || undefined,
    }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tournaments"] });
      setCreating(false);
      setName(""); setFrom(""); setTo("");
    },
  });

  if (list.isLoading) return <Spinner />;

  return (
    <div className="stack">
      <p className="section-lede">
        Create a tournament, register teams, generate the draw, and run it across
        as many courts as you have.
      </p>

      <ErrorNote error={list.error} />

      {list.data?.length === 0 ? (
        <Empty
          icon={<Trophy size={30} />}
          title="No tournaments yet"
          sub="Create one to get started."
        />
      ) : (
        list.data?.map((t) => (
          <Link className="tourney-row" key={t.id} to={`/tournaments/${t.id}`}>
            <div className="tourney-main">
              <span className="tourney-name">{t.name}</span>
              <span className="tourney-meta">
                {t.status}
                {t.starts_on ? ` · ${t.starts_on}` : ""}
                {t.ends_on ? ` → ${t.ends_on}` : ""}
              </span>
            </div>
            <ChevronRight size={18} />
          </Link>
        ))
      )}

      <button className="btn btn-primary btn-block" onClick={() => setCreating(true)}>
        <Plus size={18} /> New tournament
      </button>
      <button className="btn btn-line btn-block" onClick={() => navigate("/import")}>
        <Upload size={17} /> Upload a spreadsheet
      </button>
      <p className="foot-note">
        Registering a big event by hand is slow. Upload the entry sheet you
        already have and it becomes divisions, teams and players in one go.
      </p>

      <Sheet open={creating} onClose={() => setCreating(false)} title="New tournament">
        <input
          className="input" placeholder="Tournament name" value={name} autoFocus
          onChange={(e) => setName(e.target.value)}
        />
        <div className="tiny-label" style={{ marginTop: 12 }}>
          <CalendarDays size={13} /> Dates
        </div>
        <div className="date-row">
          <input className="input" type="date" value={from}
            onChange={(e) => setFrom(e.target.value)} />
          <span className="date-arrow">→</span>
          <input className="input" type="date" value={to}
            onChange={(e) => setTo(e.target.value)} />
        </div>
        <ErrorNote error={create.error} />
        <div className="sheet-btns">
          <button className="btn btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
          <button
            className="btn btn-primary"
            disabled={!name.trim() || create.isPending}
            onClick={() => create.mutate()}
          >
            Create
          </button>
        </div>
      </Sheet>
    </div>
  );
}
