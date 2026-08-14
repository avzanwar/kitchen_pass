import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ChevronLeft, Download, FileSpreadsheet, Info, Upload, X,
} from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Empty, ErrorNote, Spinner, cx } from "../components/ui";
import { api, previewFromError, type ImportPreview } from "../lib/api";

/**
 * Upload a spreadsheet of divisions, teams and players.
 *
 * The flow is deliberately two-step. Bulk creation is the one place in the app
 * where a single tap can produce fifty rows, so the organizer sees exactly what
 * the file was understood to say — division by division, team by team, with
 * every assumption listed — before anything is written.
 */
export default function BulkImport() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const tournamentId = params.get("tournament") ?? undefined;

  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const preview = useMutation({
    mutationFn: (chosen: File) =>
      api.importPreview(chosen, { tournamentId, name }),
  });

  const commit = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("Choose a file first");
      return api.importCommit(file, { tournamentId, name });
    },
    onSuccess: (result) => {
      void qc.invalidateQueries({ queryKey: ["tournaments"] });
      void qc.invalidateQueries({ queryKey: ["players"] });
      void qc.invalidateQueries({ queryKey: ["divisions", result.tournament.id] });
      navigate(`/tournaments/${result.tournament.id}`);
    },
  });

  const choose = (chosen: File | null) => {
    setFile(chosen);
    commit.reset();
    if (chosen) preview.mutate(chosen);
    else preview.reset();
  };

  // A rejected commit carries the same shape the preview does, so the row-by-row
  // list stays on screen instead of collapsing to a one-line error.
  const report: ImportPreview | null =
    previewFromError(commit.error) ?? preview.data ?? null;

  const needsName = !tournamentId && !name.trim();
  const canImport =
    Boolean(file) && Boolean(report?.ok) && !needsName && !commit.isPending;

  return (
    <div className="stack">
      <div className="detail-head">
        <button className="icon-btn" aria-label="Back"
          onClick={() => navigate(tournamentId ? `/tournaments/${tournamentId}` : "/")}>
          <ChevronLeft size={20} />
        </button>
        <div className="detail-title">
          <h2>Bulk upload</h2>
          <span>
            {tournamentId
              ? "Add divisions and teams to this tournament"
              : "Create a whole tournament from a spreadsheet"}
          </span>
        </div>
      </div>

      <TemplateCard />

      {!tournamentId && (
        <>
          <div className="tiny-label" style={{ marginTop: 4 }}>Tournament name</div>
          <input className="input" placeholder="e.g. Spring Open 2026" value={name}
            onChange={(e) => setName(e.target.value)} />
        </>
      )}

      <div
        className={cx("drop", dragging && "drop-on", file && "drop-filled")}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          choose(e.dataTransfer.files[0] ?? null);
        }}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,.xlsm,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          hidden
          onChange={(e) => choose(e.target.files?.[0] ?? null)}
        />
        {file ? (
          <>
            <FileSpreadsheet size={26} />
            <span className="drop-name">{file.name}</span>
            <span className="drop-hint">{(file.size / 1024).toFixed(0)} KB · tap to replace</span>
          </>
        ) : (
          <>
            <Upload size={26} />
            <span className="drop-name">Choose a file</span>
            <span className="drop-hint">or drag it here · .xlsx or .csv</span>
          </>
        )}
      </div>

      {preview.isPending && <Spinner label="Reading your sheet…" />}
      <ErrorNote error={preview.error} />
      {commit.error && !previewFromError(commit.error) && (
        <ErrorNote error={commit.error} />
      )}

      {report && <Report preview={report} />}

      {report && (
        <>
          <button className="btn btn-primary btn-block" disabled={!canImport}
            onClick={() => commit.mutate()}>
            {commit.isPending
              ? "Importing…"
              : `Import ${report.entry_count} team${report.entry_count === 1 ? "" : "s"}`}
          </button>
          {needsName && (
            <p className="foot-note">Name the tournament above to import.</p>
          )}
          {!report.ok && (
            <p className="foot-note">
              Fix the errors in your sheet and upload it again. Nothing has been
              created — an import either lands in full or not at all.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function TemplateCard() {
  return (
    <div className="tmpl-card">
      <div className="tmpl-head">
        <FileSpreadsheet size={18} />
        <span>Start from the template</span>
      </div>
      <p className="tmpl-text">
        One row per team. Rows sharing a division name become one division, and
        its settings are read from the first row that names it. The sample rows
        import as a working tournament, so you can try it before editing.
      </p>
      <div className="tmpl-btns">
        <a className="btn btn-line btn-sm" href={api.templateUrl("xlsx")} download>
          <Download size={15} /> Excel
        </a>
        <a className="btn btn-line btn-sm" href={api.templateUrl("csv")} download>
          <Download size={15} /> CSV
        </a>
      </div>
      <details className="tmpl-cols">
        <summary>Which columns?</summary>
        <dl>
          <dt>Division<em>required</em></dt>
          <dd>Event name. Rows sharing it become one division.</dd>
          <dt>Player 1<em>required</em></dt>
          <dd>Full name. Serves first from the right at 0-0.</dd>
          <dt>Player 2</dt>
          <dd>Partner. Needed for doubles and mixed, blank for singles.</dd>
          <dt>Format</dt>
          <dd>doubles, singles or mixed. Defaults to doubles.</dd>
          <dt>Draw</dt>
          <dd>round robin, single elim, double elim or pools.</dd>
          <dt>Pools · Best of</dt>
          <dd>Pool count for a pools draw; games per match (1, 3 or 5).</dd>
          <dt>Team · Seed</dt>
          <dd>Optional. A blank team is named from the players' first names.</dd>
          <dt>Rating 1 · Rating 2 · Skill · Age</dt>
          <dd>All optional.</dd>
        </dl>
        <p className="foot-note">
          Column order does not matter, spelling is matched loosely
          ("player_1", "P1"), and unknown columns are ignored.
        </p>
      </details>
    </div>
  );
}

function Report({ preview }: { preview: ImportPreview }) {
  const errors = preview.problems.filter((p) => p.severity === "error");
  const warnings = preview.problems.filter((p) => p.severity === "warning");

  if (preview.divisions.length === 0 && errors.length === 0) {
    return <Empty icon={<FileSpreadsheet size={26} />} title="Nothing to import"
      sub="That sheet had no rows we could read." />;
  }

  return (
    <>
      <div className={cx("imp-summary", !preview.ok && "imp-summary-bad")}>
        <Stat n={preview.divisions.length} label="division" />
        <Stat n={preview.entry_count} label="team" />
        <Stat n={preview.new_players} label="new player" />
        {preview.matched_players > 0 && (
          <Stat n={preview.matched_players} label="matched" plural="matched" />
        )}
      </div>

      {errors.length > 0 && (
        <ProblemList kind="error" title="Must be fixed" problems={errors} />
      )}
      {warnings.length > 0 && (
        <ProblemList kind="warning" title="Worth a look" problems={warnings} />
      )}

      {preview.divisions.map((d) => (
        <div className="imp-div" key={d.name}>
          <div className="imp-div-head">
            <span className="imp-div-name">{d.name}</span>
            {d.existing && <em className="rating-tag">adds to existing</em>}
          </div>
          <div className="imp-div-meta">
            {d.format} · {d.draw_kind.replace(/_/g, " ")}
            {d.draw_kind === "pool_playoff" ? ` · ${d.pools} pools` : ""}
            {" · "}{d.best_of === 1 ? "1 game" : `best of ${d.best_of}`}
            {d.skill ? ` · ${d.skill}` : ""}{d.age ? ` · ${d.age}` : ""}
          </div>
          {d.entries.map((e) => (
            <div className="imp-entry" key={`${e.row}-${e.name}`}>
              <span className="imp-seed">{e.seed ?? "–"}</span>
              <span className="imp-entry-name">{e.name}</span>
              <span className="imp-entry-players">
                {e.players.map((p) => (
                  <span key={p.name} className={cx("imp-player", p.existing && "known")}>
                    {p.name}
                  </span>
                ))}
              </span>
            </div>
          ))}
        </div>
      ))}
      {preview.matched_players > 0 && (
        <p className="foot-note">
          Highlighted names already exist on your roster and will be reused
          rather than added again.
        </p>
      )}
    </>
  );
}

function Stat({ n, label, plural }: { n: number; label: string; plural?: string }) {
  return (
    <div className="imp-stat">
      <b>{n}</b>
      <span>{n === 1 ? label : (plural ?? `${label}s`)}</span>
    </div>
  );
}

function ProblemList({ kind, title, problems }: {
  kind: "error" | "warning";
  title: string;
  problems: ImportPreview["problems"];
}) {
  const [open, setOpen] = useState(kind === "error");
  const Icon = kind === "error" ? X : AlertTriangle;

  return (
    <div className={cx("imp-problems", `imp-${kind}`)}>
      <button className="imp-problems-head" onClick={() => setOpen((v) => !v)}>
        {kind === "error" ? <Icon size={15} /> : <Info size={15} />}
        <span>{title} ({problems.length})</span>
        <em>{open ? "hide" : "show"}</em>
      </button>
      {open && (
        <ul>
          {problems.map((p, i) => (
            <li key={i}>
              {p.row !== null && <b>Row {p.row}</b>}
              {p.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
