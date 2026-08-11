/**
 * Read-only spectator view, reached with the tournament's share token.
 *
 * No account required, and it subscribes to the board WebSocket so a screen on
 * the fence updates itself as results land.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { Ball, Empty, ErrorNote, Spinner, cx } from "../components/ui";
import { api } from "../lib/api";

export default function PublicView() {
  const { token = "" } = useParams();
  const qc = useQueryClient();
  const [divisionId, setDivisionId] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  const overview = useQuery({
    queryKey: ["public", token],
    queryFn: () => api.publicOverview(token),
    refetchInterval: 30_000,
  });

  const board = useQuery({
    queryKey: ["public-board", token],
    queryFn: () => api.publicBoard(token),
    refetchInterval: 20_000,
  });

  const division = useQuery({
    queryKey: ["public-division", token, divisionId],
    queryFn: () => api.publicDivision(token, divisionId as string),
    enabled: Boolean(divisionId),
    refetchInterval: 15_000,
  });

  // Live nudge: the socket tells us something changed, then we refetch. Simpler
  // and more robust than trying to patch cached state from the event itself.
  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(
      `${proto}://${window.location.host}/api/v1/ws/tournaments/${token}/board`,
    );
    socket.onopen = () => setConnected(true);
    socket.onclose = () => setConnected(false);
    socket.onmessage = () => {
      void qc.invalidateQueries({ queryKey: ["public-board", token] });
      void qc.invalidateQueries({ queryKey: ["public-division", token] });
    };
    return () => socket.close();
  }, [token, qc]);

  if (overview.isLoading) return <Spinner />;
  if (!overview.data) {
    return (
      <div className="stack">
        <Empty icon={<Ball size={30} />} title="Tournament not found"
          sub="This share link may have been revoked." />
        <ErrorNote error={overview.error} />
      </div>
    );
  }

  const t = overview.data;

  return (
    <div className="stack">
      <div className="public-head">
        <div className="brand"><Ball size={22} /><span>Kitchen Pass</span></div>
        <h1 className="topbar-title">{t.name}</h1>
        <span className={cx("live-badge", connected && "on")}>
          {connected ? "live" : "reconnecting…"}
        </span>
      </div>

      {board.data && board.data.courts.length > 0 && (
        <>
          <div className="tiny-label">On court</div>
          {board.data.courts.map((c) => (
            <div className="court-card" key={c.id}>
              <div className="court-card-head">{c.name}</div>
              {c.matches.length === 0
                ? <p className="foot-note">Free</p>
                : c.matches.map((m) => (
                  <div className="court-match" key={m.match_id}>
                    <span className="court-match-teams">{m.a} <em>vs</em> {m.b}</span>
                    <span className="court-match-meta">{m.division} · {m.status}</span>
                  </div>
                ))}
            </div>
          ))}
        </>
      )}

      <div className="tiny-label" style={{ marginTop: 12 }}>Divisions</div>
      <div className="draw-grid">
        {t.divisions.map((d) => (
          <button key={d.id}
            className={cx("draw-cell", divisionId === d.id && "on")}
            onClick={() => setDivisionId(divisionId === d.id ? null : d.id)}>
            {d.name}
          </button>
        ))}
      </div>

      {division.data && (
        <>
          {division.data.standings.map((table) => (
            <div key={table.pool ?? "all"}>
              <div className="tiny-label" style={{ marginTop: 10 }}>
                {table.pool ? `Pool ${table.pool}` : "Standings"}
              </div>
              <div className="table">
                <div className="table-head">
                  <span>Team</span><span>W</span><span>L</span><span>+/−</span>
                </div>
                {table.rows.map((row) => (
                  <div className="table-row" key={row.entry_id}>
                    <span className="t-name">{row.entry_name}</span>
                    <span>{row.wins}</span><span>{row.losses}</span>
                    <span>{row.point_diff >= 0 ? "+" : ""}{row.point_diff}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}

          <div className="tiny-label" style={{ marginTop: 12 }}>Results</div>
          {division.data.matches
            .filter((m) => m.status === "complete" || m.status === "live")
            .map((m) => (
              <div className={cx("sched-row", m.status === "complete" && "done")} key={m.id}>
                <div className="sched-main">
                  <span className="sched-teams">{m.a} <em>vs</em> {m.b}</span>
                  <span className="sched-fmt">
                    {m.games.map((g) => `${g.a}-${g.b}`).join(", ") || m.status}
                    {m.winner ? ` · ${m.winner} won` : ""}
                  </span>
                </div>
              </div>
            ))}

          <div className="export-row">
            <a className="btn btn-line btn-sm"
              href={`/api/v1/public/${token}/divisions/${division.data.id}/standings.csv`}>
              Standings CSV
            </a>
            <a className="btn btn-line btn-sm"
              href={`/api/v1/public/${token}/divisions/${division.data.id}/results.csv`}>
              Results CSV
            </a>
          </div>
        </>
      )}
    </div>
  );
}
