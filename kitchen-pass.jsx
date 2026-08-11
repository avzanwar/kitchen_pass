import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Zap, Users, CalendarDays, Trophy, Clock, Plus, X, Check, Undo2,
  RotateCcw, ArrowLeftRight, Camera, ChevronRight, ChevronLeft, Coins,
  Play, Trash2, Pencil, Medal, Flag, CircleDot,
} from "lucide-react";

/* ============================================================
   Kitchen Pass — pickleball scorekeeper (replica)
   Everything runs on-device. Real side-out scoring, right down
   to which side the server stands on.
   ============================================================ */

const TARGETS = [11, 15, 21];
const PALETTE = ["#0E7C6B", "#EA6D3A", "#3B7DC4", "#B4529E", "#D99A00", "#5B8A3A", "#C0453B", "#6D5BD0"];
const EMOJIS = ["🏓", "🎾", "🔥", "⭐", "🦅", "🐅", "🚀", "🦈", "⚡", "🌊", "🥇", "🧢"];
const KEYS = {
  players: "kp:players", history: "kp:history", schedule: "kp:schedule",
  tournaments: "kp:tournaments", active: "kp:active", profile: "kp:profile",
};

const uid = () => Math.random().toString(36).slice(2, 9) + Date.now().toString(36).slice(-3);
const cx = (...a) => a.filter(Boolean).join(" ");
const keyOf = (p) => (p?.id ? "id:" + p.id : "nm:" + (p?.name || "").trim().toLowerCase());

/* ---------- storage (safe, degrades to in-memory) ---------- */
async function loadKey(key, fallback) {
  try {
    if (!window.storage) return fallback;
    const r = await window.storage.get(key, false);
    return r && r.value != null ? JSON.parse(r.value) : fallback;
  } catch { return fallback; }
}
async function saveKey(key, value) {
  try { if (window.storage) await window.storage.set(key, JSON.stringify(value), false); } catch {}
}
async function delKey(key) {
  try { if (window.storage) await window.storage.delete(key, false); } catch {}
}

/* ---------- image downscale for avatars ---------- */
function fileToAvatar(file) {
  return new Promise((res) => {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        const S = 160;
        const c = document.createElement("canvas");
        c.width = S; c.height = S;
        const ctx = c.getContext("2d");
        const scale = Math.max(S / img.width, S / img.height);
        const w = img.width * scale, h = img.height * scale;
        ctx.drawImage(img, (S - w) / 2, (S - h) / 2, w, h);
        res(c.toDataURL("image/jpeg", 0.72));
      };
      img.onerror = () => res(null);
      img.src = reader.result;
    };
    reader.onerror = () => res(null);
    reader.readAsDataURL(file);
  });
}

/* ============================================================
   SCORING ENGINE  (pure)
   ------------------------------------------------------------
   pos[team]  = [rightPlayerIndex, leftPlayerIndex]  (doubles)
   serverIdx  = physical player index (0|1) currently serving
   serverNum  = 1 | 2  (starts at 2 for the first-serving team)
   Serving team scores on a won rally; loses serve on a fault.
   ============================================================ */
function makeGame(setup) {
  const { format, target, winBy2, firstServer, teams, context } = setup;
  return {
    id: uid(),
    format, target, winBy2,
    teams: { A: teams.A, B: teams.B },
    score: { A: 0, B: 0 },
    servingTeam: firstServer,
    serverIdx: 0,                     // right-side player serves first
    serverNum: format === "doubles" ? 2 : 1, // start-of-game single-server exception
    pos: { A: [0, 1], B: [0, 1] },
    servePts: {}, serveNames: {},
    startedAt: Date.now(),
    status: "live", winner: null, endedEarly: false,
    context: context || { type: "casual" },
  };
}
function curServerPlayer(g) {
  const t = g.servingTeam;
  return g.format === "singles" ? g.teams[t].players[0] : g.teams[t].players[g.serverIdx];
}
function serveSide(g) {
  const t = g.servingTeam;
  if (g.format === "singles") return g.score[t] % 2 === 0 ? "R" : "L";
  return g.serverIdx === g.pos[t][0] ? "R" : "L";
}
function callStr(g) {
  const t = g.servingTeam, o = t === "A" ? "B" : "A";
  return g.format === "singles"
    ? `${g.score[t]}–${g.score[o]}`
    : `${g.score[t]}–${g.score[o]}–${g.serverNum}`;
}
function applyResult(g, servingWon) {
  const n = JSON.parse(JSON.stringify(g));
  const t = n.servingTeam, o = t === "A" ? "B" : "A";
  if (servingWon) {
    n.score[t] += 1;
    const srv = curServerPlayer(n);
    const k = keyOf(srv);
    n.servePts[k] = (n.servePts[k] || 0) + 1;
    n.serveNames[k] = srv.name;
    if (n.format === "doubles") { const p = n.pos[t]; n.pos[t] = [p[1], p[0]]; }
    const s = n.score[t], oo = n.score[o];
    if (s >= n.target && (!n.winBy2 || s - oo >= 2)) { n.status = "won"; n.winner = t; }
  } else {
    if (n.format === "singles") {
      n.servingTeam = o;
    } else if (n.serverNum === 1) {
      n.serverNum = 2; n.serverIdx = 1 - n.serverIdx;
    } else {
      n.servingTeam = o; n.serverNum = 1; n.serverIdx = n.pos[o][0];
    }
  }
  return n;
}

/* ============================================================
   Small presentational pieces
   ============================================================ */
function Ball({ size = 22, glow = false }) {
  const r = size / 2;
  const holes = [
    [0.5, 0.22], [0.28, 0.4], [0.72, 0.4], [0.5, 0.55],
    [0.34, 0.7], [0.66, 0.7], [0.5, 0.84],
  ];
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" className={glow ? "ball-glow" : ""}>
      <circle cx="50" cy="50" r="47" fill="var(--ball)" stroke="var(--ball-deep)" strokeWidth="3" />
      <ellipse cx="38" cy="34" rx="16" ry="10" fill="#ffffff" opacity="0.28" />
      {holes.map(([x, y], i) => (
        <circle key={i} cx={x * 100} cy={y * 100} r="5.5" fill="var(--ball-deep)" opacity="0.85" />
      ))}
    </svg>
  );
}

function Avatar({ player, size = 40 }) {
  const style = { width: size, height: size, fontSize: size * 0.42 };
  if (!player) return <div className="avatar avatar-empty" style={style}>?</div>;
  if (player.avatar?.type === "photo")
    return <img className="avatar" style={style} src={player.avatar.value} alt="" />;
  if (player.avatar?.type === "emoji")
    return <div className="avatar" style={{ ...style, background: player.avatar.color || "#e6e9e3" }}>{player.avatar.value}</div>;
  const init = (player.name || "?").trim().slice(0, 1).toUpperCase();
  return <div className="avatar" style={{ ...style, background: player.avatar?.color || "#0E7C6B", color: "#fff", fontWeight: 700 }}>{init}</div>;
}

function Seg({ options, value, onChange, small }) {
  return (
    <div className={cx("seg", small && "seg-sm")}>
      {options.map((o) => (
        <button key={o.value} className={cx("seg-btn", value === o.value && "seg-on")}
          onClick={() => onChange(o.value)} type="button">{o.label}</button>
      ))}
    </div>
  );
}

function Toggle({ on, onChange, label, hint }) {
  return (
    <button type="button" className="toggle-row" onClick={() => onChange(!on)}>
      <div className="toggle-text">
        <span className="toggle-label">{label}</span>
        {hint && <span className="toggle-hint">{hint}</span>}
      </div>
      <span className={cx("switch", on && "switch-on")}><span className="knob" /></span>
    </button>
  );
}

function Sheet({ open, onClose, title, children }) {
  if (!open) return null;
  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-grip" />
        <div className="sheet-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose}><X size={20} /></button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );
}

function Empty({ icon, title, sub }) {
  return (
    <div className="empty">
      <div className="empty-ico">{icon}</div>
      <p className="empty-title">{title}</p>
      <p className="empty-sub">{sub}</p>
    </div>
  );
}

/* ============================================================
   APP
   ============================================================ */
export default function App() {
  const [loaded, setLoaded] = useState(false);
  const [entered, setEntered] = useState(false);
  const [tab, setTab] = useState("play");

  const [players, setPlayers] = useState([]);
  const [history, setHistory] = useState([]);
  const [schedule, setSchedule] = useState([]);
  const [tournaments, setTournaments] = useState([]);
  const [profile, setProfile] = useState("");

  const [game, setGame] = useState(null);
  const [undoStack, setUndoStack] = useState([]);

  const loadedRef = useRef(false);

  /* -------- initial load -------- */
  useEffect(() => {
    (async () => {
      const [pl, hi, sc, tn, ac, pr] = await Promise.all([
        loadKey(KEYS.players, []), loadKey(KEYS.history, []), loadKey(KEYS.schedule, []),
        loadKey(KEYS.tournaments, []), loadKey(KEYS.active, null), loadKey(KEYS.profile, ""),
      ]);
      setPlayers(pl); setHistory(hi); setSchedule(sc); setTournaments(tn); setProfile(pr);
      if (ac) { setGame(ac); setEntered(true); }
      loadedRef.current = true;
      setLoaded(true);
    })();
  }, []);

  /* -------- write-through -------- */
  useEffect(() => { if (loadedRef.current) saveKey(KEYS.players, players); }, [players]);
  useEffect(() => { if (loadedRef.current) saveKey(KEYS.history, history); }, [history]);
  useEffect(() => { if (loadedRef.current) saveKey(KEYS.schedule, schedule); }, [schedule]);
  useEffect(() => { if (loadedRef.current) saveKey(KEYS.tournaments, tournaments); }, [tournaments]);
  useEffect(() => { if (loadedRef.current) saveKey(KEYS.profile, profile); }, [profile]);
  useEffect(() => {
    if (!loadedRef.current) return;
    if (game && game.status === "live") saveKey(KEYS.active, game);
    else delKey(KEYS.active);
  }, [game]);

  /* -------- game actions -------- */
  const startGame = useCallback((setup) => {
    const g = makeGame(setup);
    setUndoStack([]);
    setGame(g);
  }, []);

  const doResult = useCallback((servingWon) => {
    setGame((g) => {
      if (!g || g.status !== "live") return g;
      setUndoStack((s) => [...s, g]);
      return applyResult(g, servingWon);
    });
  }, []);

  const undo = useCallback(() => {
    setUndoStack((s) => {
      if (!s.length) return s;
      setGame(s[s.length - 1]);
      return s.slice(0, -1);
    });
  }, []);

  const swapPartners = useCallback((team) => {
    setGame((g) => {
      if (!g || g.format !== "doubles") return g;
      const n = JSON.parse(JSON.stringify(g));
      n.teams[team].players = [n.teams[team].players[1], n.teams[team].players[0]];
      return n;
    });
  }, []);

  const finishGame = useCallback((g, endedEarly) => {
    const rec = {
      id: g.id, endedAt: Date.now(), format: g.format, target: g.target,
      teams: g.teams, score: g.score, winner: g.winner, servePts: g.servePts,
      serveNames: g.serveNames, endedEarly,
    };
    setHistory((h) => [rec, ...h]);

    // route result back to schedule / tournament
    if (g.context?.type === "schedule" && g.context.matchId) {
      setSchedule((sc) => sc.map((m) => m.id === g.context.matchId
        ? { ...m, done: true, result: { winner: g.winner, score: g.score } } : m));
    }
    if (g.context?.type === "tournament" && g.context.tournamentId && g.context.matchId) {
      setTournaments((ts) => ts.map((t) => {
        if (t.id !== g.context.tournamentId) return t;
        return {
          ...t,
          matches: t.matches.map((m) => m.id === g.context.matchId
            ? { ...m, result: { winner: g.winner, score: { ...g.score }, servePts: g.servePts, serveNames: g.serveNames } }
            : m),
        };
      }));
    }
    setGame(null);
    setUndoStack([]);
  }, []);

  /* -------- players CRUD -------- */
  const upsertPlayer = (p) => {
    setPlayers((ps) => {
      const i = ps.findIndex((x) => x.id === p.id);
      if (i === -1) return [...ps, p];
      const c = ps.slice(); c[i] = p; return c;
    });
  };
  const removePlayer = (id) => setPlayers((ps) => ps.filter((p) => p.id !== id));

  if (!loaded) {
    return (
      <div className="kp-root"><style>{CSS}</style>
        <div className="splash"><Ball size={54} glow /><p>Kitchen Pass</p></div>
      </div>
    );
  }

  if (!entered && !game) {
    return (
      <div className="kp-root"><style>{CSS}</style>
        <Landing onEnter={() => setEntered(true)} />
      </div>
    );
  }

  return (
    <div className="kp-root"><style>{CSS}</style>
      <div className="app">
        {game ? (
          <LiveGame
            game={game} onResult={doResult} onUndo={undo} canUndo={undoStack.length > 0}
            onSwap={swapPartners} onFinish={finishGame}
          />
        ) : (
          <>
            <TopBar tab={tab} profile={profile} />
            <main className="content">
              {tab === "play" && <PlayTab players={players} onStart={startGame} />}
              {tab === "players" && <PlayersTab players={players} onSave={upsertPlayer} onRemove={removePlayer} />}
              {tab === "schedule" && <ScheduleTab schedule={schedule} setSchedule={setSchedule} players={players} onStart={startGame} />}
              {tab === "tourneys" && <TourneysTab tournaments={tournaments} setTournaments={setTournaments} players={players} profile={profile} setProfile={setProfile} onStart={startGame} />}
              {tab === "history" && <HistoryTab history={history} setHistory={setHistory} />}
            </main>
            <nav className="tabbar">
              {[
                ["play", "Play", Zap], ["players", "Players", Users],
                ["schedule", "Schedule", CalendarDays], ["tourneys", "Tourneys", Trophy],
                ["history", "History", Clock],
              ].map(([id, label, Icon]) => (
                <button key={id} className={cx("tab", tab === id && "tab-on")} onClick={() => setTab(id)}>
                  <Icon size={21} strokeWidth={tab === id ? 2.4 : 2} />
                  <span>{label}</span>
                </button>
              ))}
            </nav>
          </>
        )}
      </div>
    </div>
  );
}

/* ============================================================
   Landing
   ============================================================ */
function Landing({ onEnter }) {
  const [note, setNote] = useState("");
  return (
    <div className="landing">
      <div className="landing-court">
        <div className="court-line court-net" />
        <div className="court-line court-kitchen-top" />
        <div className="court-line court-kitchen-bot" />
      </div>
      <div className="landing-inner">
        <div className="brand-big"><Ball size={46} glow /><span>Kitchen Pass</span></div>
        <p className="landing-lede">
          Keep accurate, rules-based scores — right down to which side the server stands on. Set up games, run tournaments, and track your regulars, all from your phone.
        </p>
        <div className="landing-actions">
          <button className="btn btn-primary" onClick={() => setNote("Account sync isn't wired up in this replica — your data is saved on this device instead.")}>Sign in</button>
          <button className="btn btn-ghost" onClick={() => setNote("Account sync isn't wired up in this replica — your data is saved on this device instead.")}>Create account</button>
          <button className="btn btn-line" onClick={onEnter}>Continue without an account</button>
        </div>
        {note && (
          <div className="note">
            {note}
            <button className="btn btn-primary btn-sm" onClick={onEnter}>Continue on this device</button>
          </div>
        )}
        <p className="build">Build 2026-08-11 · replica</p>
      </div>
    </div>
  );
}

/* ============================================================
   Top bar
   ============================================================ */
function TopBar({ tab, profile }) {
  const titles = { play: "New game", players: "Players", schedule: "Schedule", tourneys: "Tournaments", history: "History" };
  return (
    <header className="topbar">
      <div className="brand"><Ball size={22} /><span>Kitchen Pass</span></div>
      <h1 className="topbar-title">{titles[tab]}</h1>
    </header>
  );
}

/* ============================================================
   Player picker sheet (fill a slot)
   ============================================================ */
function PlayerPicker({ open, onClose, players, onPick }) {
  const [name, setName] = useState("");
  const add = () => {
    const nm = name.trim();
    if (!nm) return;
    onPick({ name: nm, avatar: { type: "initials", color: PALETTE[nm.length % PALETTE.length] } });
    setName(""); onClose();
  };
  return (
    <Sheet open={open} onClose={onClose} title="Choose a player">
      {players.length > 0 && (
        <div className="pick-grid">
          {players.map((p) => (
            <button key={p.id} className="pick-cell" onClick={() => { onPick(p); onClose(); }}>
              <Avatar player={p} size={46} />
              <span>{p.name}</span>
            </button>
          ))}
        </div>
      )}
      <div className="pick-adhoc">
        <input className="input" placeholder="…or type a name for a one-off" value={name}
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        <button className="btn btn-primary btn-sm" onClick={add}>Add</button>
      </div>
    </Sheet>
  );
}

function TeamSlots({ teamKey, label, count, values, onChange, players }) {
  const [picking, setPicking] = useState(null); // slot index
  const set = (i, v) => { const c = values.slice(); c[i] = v; onChange(c); };
  return (
    <div className="team-card">
      <div className="team-card-head"><span className="team-dot" data-team={teamKey} />{label}</div>
      <div className="slots">
        {Array.from({ length: count }).map((_, i) => {
          const p = values[i];
          return p ? (
            <div key={i} className="slot slot-filled">
              <Avatar player={p} size={34} />
              <span className="slot-name">{p.name}</span>
              <button className="icon-btn sm" onClick={() => set(i, null)}><X size={16} /></button>
            </div>
          ) : (
            <button key={i} className="slot slot-add" onClick={() => setPicking(i)}>
              <Plus size={17} /> Add player
            </button>
          );
        })}
      </div>
      <PlayerPicker open={picking !== null} onClose={() => setPicking(null)} players={players}
        onPick={(pl) => set(picking, pl)} />
    </div>
  );
}

/* ============================================================
   PLAY tab — new game setup + coin toss
   ============================================================ */
function CoinToss({ firstServer, teamAName, teamBName, onSet }) {
  const [flipping, setFlipping] = useState(false);
  const [stage, setStage] = useState("idle"); // idle | won
  const [winner, setWinner] = useState(null);
  const flip = () => {
    setFlipping(true); setStage("idle");
    setTimeout(() => {
      const w = Math.random() < 0.5 ? "A" : "B";
      setWinner(w); setFlipping(false); setStage("won");
    }, 900);
  };
  const wName = winner === "A" ? teamAName : teamBName;
  const lName = winner === "A" ? teamBName : teamAName;
  const loser = winner === "A" ? "B" : "A";
  return (
    <div className="card">
      <div className="card-title"><Coins size={17} /> Coin toss</div>
      <p className="card-sub">Flip to decide who serves first — the winner chooses to serve or to pick a side.</p>
      <div className="toss-stage">
        <div className={cx("coin", flipping && "coin-flip")}><Ball size={62} glow /></div>
      </div>
      <button className="btn btn-line btn-block" onClick={flip} disabled={flipping}>
        {stage === "idle" && !flipping ? "🪙 Flip coin" : flipping ? "Flipping…" : "Redo toss"}
      </button>
      {stage === "won" && (
        <div className="toss-result">
          <p><b>{wName}</b> won the toss.</p>
          <div className="toss-choices">
            <button className="btn btn-primary btn-sm" onClick={() => onSet(winner)}>{wName} serves first</button>
            <button className="btn btn-ghost btn-sm" onClick={() => onSet(loser)}>
              Pick a side → {lName} serves
            </button>
          </div>
        </div>
      )}
      <div className="firstserve">
        <span className="tiny-label">Serving first</span>
        <Seg small options={[{ value: "A", label: teamAName }, { value: "B", label: teamBName }]}
          value={firstServer} onChange={onSet} />
      </div>
    </div>
  );
}

function PlayTab({ players, onStart, prefill, onConsumePrefill }) {
  const [format, setFormat] = useState("doubles");
  const [target, setTarget] = useState(11);
  const [winBy2, setWinBy2] = useState(true);
  const [firstServer, setFirstServer] = useState("A");
  const [aName, setAName] = useState("Team A");
  const [bName, setBName] = useState("Team B");
  const [aPlayers, setAPlayers] = useState([null, null]);
  const [bPlayers, setBPlayers] = useState([null, null]);
  const [err, setErr] = useState("");

  const count = format === "doubles" ? 2 : 1;

  const teamLabel = (side, players) => {
    const named = players.filter(Boolean).map((p) => p.name.split(" ")[0]);
    return named.length ? named.join(" & ") : side === "A" ? "Team A" : "Team B";
  };

  const start = () => {
    const need = format === "doubles" ? 2 : 1;
    const a = aPlayers.slice(0, need).filter(Boolean);
    const b = bPlayers.slice(0, need).filter(Boolean);
    if (a.length < need || b.length < need) {
      setErr(`Pick ${need} player${need > 1 ? "s" : ""} for each team.`);
      return;
    }
    onStart({
      format, target, winBy2, firstServer,
      teams: {
        A: { name: teamLabel("A", a), players: a },
        B: { name: teamLabel("B", b), players: b },
      },
      context: { type: "casual" },
    });
  };

  return (
    <div className="stack">
      <div className="card">
        <div className="tiny-label">Format</div>
        <Seg options={[{ value: "doubles", label: "Doubles" }, { value: "singles", label: "Singles" }]}
          value={format} onChange={setFormat} />
      </div>

      <TeamSlots teamKey="A" label="Team A" count={count} values={aPlayers} onChange={setAPlayers} players={players} />
      <TeamSlots teamKey="B" label="Team B" count={count} values={bPlayers} onChange={setBPlayers} players={players} />

      <CoinToss firstServer={firstServer} onSet={setFirstServer}
        teamAName={teamLabel("A", aPlayers)} teamBName={teamLabel("B", bPlayers)} />

      <div className="card">
        <div className="row-between">
          <span className="tiny-label">Play to</span>
          <Seg small options={TARGETS.map((t) => ({ value: t, label: String(t) }))} value={target} onChange={setTarget} />
        </div>
        <div className="divider" />
        <Toggle on={winBy2} onChange={setWinBy2} label="Win by 2"
          hint="Game continues past the target until the lead is 2" />
      </div>

      {err && <div className="err">{err}</div>}
      <button className="btn btn-primary btn-block btn-lg" onClick={start}>
        <Play size={18} /> Start game
      </button>
      <p className="foot-note">Side-out scoring: only the serving team can score. The scoreboard tracks the server number and which side they stand on.</p>
    </div>
  );
}

/* ============================================================
   LIVE GAME
   ============================================================ */
function LiveGame({ game, onResult, onUndo, canUndo, onSwap, onFinish }) {
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [pulse, setPulse] = useState(null);
  const prevScore = useRef(game.score);

  useEffect(() => {
    const p = prevScore.current;
    if (game.score.A > p.A) setPulse("A");
    else if (game.score.B > p.B) setPulse("B");
    prevScore.current = game.score;
    if (pulse) { const t = setTimeout(() => setPulse(null), 360); return () => clearTimeout(t); }
  }, [game.score]); // eslint-disable-line

  if (game.status === "won") return <WonScreen game={game} onFinish={onFinish} />;

  const t = game.servingTeam;
  const server = curServerPlayer(game);
  const side = serveSide(game);
  const isDoubles = game.format === "doubles";

  const TeamBlock = ({ team }) => {
    const serving = team === t;
    const tp = game.teams[team];
    return (
      <div className={cx("score-team", serving && "serving", pulse === team && "pulsing")} data-team={team}>
        <div className="score-team-top">
          <div className="score-team-players">
            {tp.players.map((p, i) => {
              const isSrv = serving && (isDoubles ? game.serverIdx === i : true);
              return (
                <div key={i} className={cx("pl-chip", isSrv && "pl-serving")}>
                  <Avatar player={p} size={26} />
                  <span>{p.name.split(" ")[0]}</span>
                  {isSrv && <span className="srv-ball"><Ball size={16} glow /></span>}
                </div>
              );
            })}
          </div>
          {isDoubles && (
            <button className="swap-btn" onClick={() => onSwap(team)} title="Swap sides">
              <ArrowLeftRight size={14} /> Swap
            </button>
          )}
        </div>
        <div className="score-num">{game.score[team]}</div>
        {serving && (
          <div className="serve-tag">
            Serving · side {side === "R" ? "right" : "left"}{isDoubles ? ` · server ${game.serverNum}` : ""}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="live">
      <div className="live-head">
        <span className="live-dot" /> Game in progress
        <span className="live-target">to {game.target}{game.winBy2 ? " · win by 2" : ""}</span>
      </div>

      <div className="scoreboard">
        <div className="court-bg">
          <div className="cb-line cb-net" /><div className="cb-line cb-k1" /><div className="cb-line cb-k2" />
        </div>
        <TeamBlock team="A" />
        <div className="score-mid">
          <div className="call">{callStr(game)}</div>
          <ServeDiagram side={side} team={t} />
        </div>
        <TeamBlock team="B" />
      </div>

      <div className="serving-banner">
        <Ball size={18} />
        <span><b>{game.teams[t].name}</b> serving — {server.name.split(" ")[0]} from the {side === "R" ? "right" : "left"}</span>
      </div>

      <div className="result-btns">
        <button className="btn-result btn-point" onClick={() => onResult(true)}>
          <span className="br-big">Point</span>
          <span className="br-sub">serving team won the rally</span>
        </button>
        <button className="btn-result btn-sideout" onClick={() => onResult(false)}>
          <span className="br-big">Side out</span>
          <span className="br-sub">serving team lost the rally</span>
        </button>
      </div>

      <div className="live-actions">
        <button className="btn btn-line btn-sm" onClick={onUndo} disabled={!canUndo}>
          <Undo2 size={16} /> Undo last
        </button>
        <button className="btn btn-line btn-sm danger" onClick={() => setConfirmEnd(true)}>
          <Flag size={15} /> End game early
        </button>
      </div>

      <Sheet open={confirmEnd} onClose={() => setConfirmEnd(false)} title="End game early?">
        <p className="sheet-text">The current score will be saved to your history. No winner will be recorded.</p>
        <div className="sheet-btns">
          <button className="btn btn-ghost" onClick={() => setConfirmEnd(false)}>Cancel</button>
          <button className="btn btn-primary danger-fill" onClick={() => onFinish(game, true)}>End game</button>
        </div>
      </Sheet>
    </div>
  );
}

function ServeDiagram({ side, team }) {
  return (
    <div className="serve-diagram" title="Server position">
      <div className="sd-half">
        <span className={cx("sd-cell", side === "L" && "sd-active")} />
        <span className={cx("sd-cell", side === "R" && "sd-active")} />
      </div>
    </div>
  );
}

function WonScreen({ game, onFinish }) {
  const w = game.winner;
  const wName = game.teams[w].name;
  const s = `${Math.max(game.score.A, game.score.B)}–${Math.min(game.score.A, game.score.B)}`;
  return (
    <div className="won">
      <div className="won-confetti"><Ball size={40} glow /></div>
      <p className="won-eyebrow">Match complete</p>
      <h2 className="won-team">{wName} wins</h2>
      <div className="won-score">{s}</div>
      <div className="won-leaders">
        {Object.keys(game.servePts).length > 0 && (
          <>
            <p className="tiny-label center">Serve points</p>
            {Object.entries(game.servePts).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, v]) => (
              <div className="won-leader-row" key={k}>
                <span>{game.serveNames[k]}</span><b>{v}</b>
              </div>
            ))}
          </>
        )}
      </div>
      <button className="btn btn-primary btn-block btn-lg" onClick={() => onFinish(game, false)}>
        <Check size={18} /> Save & finish
      </button>
    </div>
  );
}

/* ============================================================
   PLAYERS tab
   ============================================================ */
function PlayersTab({ players, onSave, onRemove }) {
  const [editing, setEditing] = useState(null); // player or {} for new
  return (
    <div className="stack">
      <p className="section-lede">Save your regulars once, then pick them by name and photo anywhere you set up a game.</p>
      {players.length === 0 ? (
        <Empty icon={<Users size={30} />} title="No players yet" sub="Add the people you play with most." />
      ) : (
        <div className="players-list">
          {players.map((p) => (
            <div className="player-row" key={p.id}>
              <Avatar player={p} size={44} />
              <span className="player-row-name">{p.name}</span>
              <button className="icon-btn" onClick={() => setEditing(p)}><Pencil size={17} /></button>
              <button className="icon-btn" onClick={() => onRemove(p.id)}><Trash2 size={17} /></button>
            </div>
          ))}
        </div>
      )}
      <button className="btn btn-primary btn-block" onClick={() => setEditing({})}>
        <Plus size={18} /> Add player
      </button>
      <PlayerEditor player={editing} onClose={() => setEditing(null)}
        onSave={(p) => { onSave(p); setEditing(null); }} />
    </div>
  );
}

function PlayerEditor({ player, onClose, onSave }) {
  const open = player !== null;
  const [name, setName] = useState("");
  const [avatar, setAvatar] = useState(null);
  const fileRef = useRef(null);

  useEffect(() => {
    if (player) { setName(player.name || ""); setAvatar(player.avatar || null); }
  }, [player]);

  const pickPhoto = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    const data = await fileToAvatar(f);
    if (data) setAvatar({ type: "photo", value: data });
  };
  const save = () => {
    const nm = name.trim();
    if (!nm) return;
    const av = avatar || { type: "initials", color: PALETTE[nm.length % PALETTE.length] };
    onSave({ id: player?.id || uid(), name: nm, avatar: av });
  };
  const preview = { name: name || "?", avatar };

  return (
    <Sheet open={open} onClose={onClose} title={player?.id ? "Edit player" : "Add player"}>
      <div className="editor">
        <div className="editor-avatar">
          <Avatar player={preview} size={84} />
        </div>
        <input className="input" placeholder="Name" value={name}
          onChange={(e) => setName(e.target.value)} autoFocus />
        <div className="tiny-label">Photo</div>
        <div className="editor-photo-row">
          <button className="btn btn-line btn-sm" onClick={() => fileRef.current?.click()}>
            <Camera size={16} /> Upload
          </button>
          {avatar?.type === "photo" && (
            <button className="btn btn-ghost btn-sm" onClick={() => setAvatar(null)}>Remove photo</button>
          )}
          <input ref={fileRef} type="file" accept="image/*" hidden onChange={pickPhoto} />
        </div>
        <div className="tiny-label">Or pick an emoji</div>
        <div className="emoji-grid">
          {EMOJIS.map((e) => (
            <button key={e} className={cx("emoji-cell", avatar?.type === "emoji" && avatar.value === e && "on")}
              onClick={() => setAvatar({ type: "emoji", value: e, color: PALETTE[EMOJIS.indexOf(e) % PALETTE.length] })}>{e}</button>
          ))}
        </div>
        <div className="sheet-btns">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={save}>Save</button>
        </div>
      </div>
    </Sheet>
  );
}

/* ============================================================
   SCHEDULE tab
   ============================================================ */
function ScheduleTab({ schedule, setSchedule, players, onStart }) {
  const [adding, setAdding] = useState(false);
  const upcoming = schedule.filter((m) => !m.done);
  const done = schedule.filter((m) => m.done);

  const play = (m) => {
    onStart({
      format: m.format, target: m.target, winBy2: true, firstServer: "A",
      teams: {
        A: { name: m.a.name, players: m.a.players },
        B: { name: m.b.name, players: m.b.players },
      },
      context: { type: "schedule", matchId: m.id },
    });
  };
  const remove = (id) => setSchedule((s) => s.filter((m) => m.id !== id));

  return (
    <div className="stack">
      <p className="section-lede">Line up several matches ahead of time — great for a court session with friends.</p>
      {upcoming.length === 0 && done.length === 0 && (
        <Empty icon={<CalendarDays size={30} />} title="Nothing scheduled" sub="Queue up matches for your next session." />
      )}
      {upcoming.map((m) => (
        <div className="sched-row" key={m.id}>
          <div className="sched-main">
            <span className="sched-fmt">{m.format === "doubles" ? "Doubles" : "Singles"} · to {m.target}</span>
            <span className="sched-teams">{m.a.name} <em>vs</em> {m.b.name}</span>
          </div>
          <button className="btn btn-primary btn-sm" onClick={() => play(m)}><Play size={15} /> Play</button>
          <button className="icon-btn" onClick={() => remove(m.id)}><X size={17} /></button>
        </div>
      ))}
      {done.length > 0 && <div className="tiny-label" style={{ marginTop: 8 }}>Played</div>}
      {done.map((m) => (
        <div className="sched-row done" key={m.id}>
          <div className="sched-main">
            <span className="sched-teams">{m.a.name} <em>vs</em> {m.b.name}</span>
            {m.result && <span className="sched-fmt">Won by {m.result.winner === "A" ? m.a.name : m.b.name} · {Math.max(m.result.score.A, m.result.score.B)}–{Math.min(m.result.score.A, m.result.score.B)}</span>}
          </div>
          <button className="icon-btn" onClick={() => remove(m.id)}><Trash2 size={16} /></button>
        </div>
      ))}
      <button className="btn btn-line btn-block" onClick={() => setAdding(true)}><Plus size={17} /> New scheduled match</button>
      <ScheduleAdd open={adding} onClose={() => setAdding(false)} players={players}
        onAdd={(m) => { setSchedule((s) => [...s, m]); setAdding(false); }} />
    </div>
  );
}

function ScheduleAdd({ open, onClose, players, onAdd }) {
  const [format, setFormat] = useState("doubles");
  const [target, setTarget] = useState(11);
  const [aP, setAP] = useState([null, null]);
  const [bP, setBP] = useState([null, null]);
  const count = format === "doubles" ? 2 : 1;
  const label = (side, arr) => {
    const n = arr.filter(Boolean).map((p) => p.name.split(" ")[0]);
    return n.length ? n.join(" & ") : side;
  };
  const add = () => {
    const need = count;
    const a = aP.slice(0, need).filter(Boolean), b = bP.slice(0, need).filter(Boolean);
    if (a.length < need || b.length < need) return;
    onAdd({
      id: uid(), format, target, done: false,
      a: { name: label("Team A", a), players: a },
      b: { name: label("Team B", b), players: b },
    });
    setAP([null, null]); setBP([null, null]);
  };
  return (
    <Sheet open={open} onClose={onClose} title="New scheduled match">
      <Seg options={[{ value: "doubles", label: "Doubles" }, { value: "singles", label: "Singles" }]} value={format} onChange={setFormat} />
      <div style={{ height: 12 }} />
      <TeamSlots teamKey="A" label="Team A" count={count} values={aP} onChange={setAP} players={players} />
      <div style={{ height: 10 }} />
      <TeamSlots teamKey="B" label="Team B" count={count} values={bP} onChange={setBP} players={players} />
      <div className="row-between" style={{ marginTop: 14 }}>
        <span className="tiny-label">Play to</span>
        <Seg small options={TARGETS.map((t) => ({ value: t, label: String(t) }))} value={target} onChange={setTarget} />
      </div>
      <div className="sheet-btns">
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={add}>Add to schedule</button>
      </div>
    </Sheet>
  );
}

/* ============================================================
   TOURNEYS tab
   ============================================================ */
function TourneysTab({ tournaments, setTournaments, players, profile, setProfile, onStart }) {
  const [selected, setSelected] = useState(null);
  const [creating, setCreating] = useState(false);
  const [editProfile, setEditProfile] = useState(false);

  const current = tournaments.find((t) => t.id === selected);
  if (current) {
    return <TournamentDetail t={current}
      onBack={() => setSelected(null)}
      onChange={(nt) => setTournaments((ts) => ts.map((x) => x.id === nt.id ? nt : x))}
      onDelete={() => { setTournaments((ts) => ts.filter((x) => x.id !== current.id)); setSelected(null); }}
      players={players} onStart={onStart} />;
  }

  return (
    <div className="stack">
      <p className="section-lede">Create a tournament or series, register teams and pairs, schedule matches, and track the serve leaderboard.</p>

      <button className="profile-chip" onClick={() => setEditProfile(true)}>
        <div className="profile-ava">{(profile || "You").slice(0, 1).toUpperCase()}</div>
        <div className="profile-txt">
          <span>{profile || "Set your name"}</span>
          <em>This just labels tournaments you create — it isn't a secure login.</em>
        </div>
        <Pencil size={15} />
      </button>

      {tournaments.length === 0 ? (
        <Empty icon={<Trophy size={30} />} title="No tournaments yet" sub="Spin up a series and register your teams." />
      ) : tournaments.map((t) => {
        const played = t.matches.filter((m) => m.result).length;
        return (
          <button className="tourney-row" key={t.id} onClick={() => setSelected(t.id)}>
            <div className="tourney-main">
              <span className="tourney-name">{t.name}</span>
              <span className="tourney-meta">{t.teams.length} teams · {played}/{t.matches.length} played · to {t.target}</span>
            </div>
            <ChevronRight size={18} />
          </button>
        );
      })}

      <button className="btn btn-primary btn-block" onClick={() => setCreating(true)}>
        <Plus size={18} /> New tournament / series
      </button>

      <TournamentCreate open={creating} onClose={() => setCreating(false)} owner={profile}
        onCreate={(t) => { setTournaments((ts) => [t, ...ts]); setCreating(false); setSelected(t.id); }} />

      <Sheet open={editProfile} onClose={() => setEditProfile(false)} title="Your profile">
        <p className="sheet-text">This just labels tournaments you create — it isn't a secure login.</p>
        <input className="input" placeholder="Your name" defaultValue={profile}
          onChange={(e) => setProfile(e.target.value)} />
        <div className="sheet-btns">
          <button className="btn btn-primary" onClick={() => setEditProfile(false)}>Save</button>
        </div>
      </Sheet>
    </div>
  );
}

function TournamentCreate({ open, onClose, owner, onCreate }) {
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [format, setFormat] = useState("doubles");
  const [target, setTarget] = useState(11);
  const create = () => {
    const nm = name.trim();
    if (!nm) return;
    onCreate({
      id: uid(), name: nm, owner: owner || "", dates: { from, to },
      format, target, winBy2: true, teams: [], matches: [], createdAt: Date.now(),
    });
    setName(""); setFrom(""); setTo("");
  };
  return (
    <Sheet open={open} onClose={onClose} title="New tournament / series">
      <input className="input" placeholder="Tournament name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      <div className="tiny-label" style={{ marginTop: 12 }}>Tournament dates</div>
      <div className="date-row">
        <input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        <span className="date-arrow">→</span>
        <input className="input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
      </div>
      <div className="tiny-label" style={{ marginTop: 12 }}>Play format</div>
      <Seg options={[{ value: "doubles", label: "Doubles" }, { value: "singles", label: "Singles" }]} value={format} onChange={setFormat} />
      <div className="row-between" style={{ marginTop: 12 }}>
        <span className="tiny-label">Play each match to</span>
        <Seg small options={TARGETS.map((t) => ({ value: t, label: String(t) }))} value={target} onChange={setTarget} />
      </div>
      <div className="sheet-btns">
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={create}>Create</button>
      </div>
    </Sheet>
  );
}

function TournamentDetail({ t, onBack, onChange, onDelete, players, onStart }) {
  const [sub, setSub] = useState("teams");
  const [addTeam, setAddTeam] = useState(false);
  const [addMatch, setAddMatch] = useState(false);

  const teamName = (id) => t.teams.find((x) => x.id === id)?.name || "—";

  // standings
  const standings = t.teams.map((tm) => {
    let w = 0, l = 0, pf = 0, pa = 0;
    t.matches.forEach((m) => {
      if (!m.result) return;
      const isA = m.aTeamId === tm.id, isB = m.bTeamId === tm.id;
      if (!isA && !isB) return;
      const my = isA ? m.result.score.A : m.result.score.B;
      const opp = isA ? m.result.score.B : m.result.score.A;
      pf += my; pa += opp;
      const won = (isA && m.result.winner === "A") || (isB && m.result.winner === "B");
      if (won) w++; else l++;
    });
    return { id: tm.id, name: tm.name, w, l, pf, pa, pts: w * 2 };
  }).sort((a, b) => b.pts - a.pts || (b.pf - b.pa) - (a.pf - a.pa));

  // serve leaderboard
  const leaders = {};
  t.matches.forEach((m) => {
    if (!m.result?.servePts) return;
    Object.entries(m.result.servePts).forEach(([k, v]) => {
      if (!leaders[k]) leaders[k] = { name: m.result.serveNames[k], pts: 0 };
      leaders[k].pts += v;
    });
  });
  const leaderRows = Object.values(leaders).sort((a, b) => b.pts - a.pts);

  const playMatch = (m) => {
    const a = t.teams.find((x) => x.id === m.aTeamId);
    const b = t.teams.find((x) => x.id === m.bTeamId);
    if (!a || !b) return;
    onStart({
      format: t.format, target: t.target, winBy2: t.winBy2, firstServer: "A",
      teams: {
        A: { name: a.name, players: a.players },
        B: { name: b.name, players: b.players },
      },
      context: { type: "tournament", tournamentId: t.id, matchId: m.id },
    });
  };

  return (
    <div className="stack">
      <div className="detail-head">
        <button className="icon-btn" onClick={onBack}><ChevronLeft size={20} /></button>
        <div className="detail-title">
          <h2>{t.name}</h2>
          <span>{t.format === "doubles" ? "Doubles" : "Singles"} · to {t.target}{t.dates?.from ? ` · ${t.dates.from}${t.dates.to ? "→" + t.dates.to : ""}` : ""}</span>
        </div>
        <button className="icon-btn" onClick={onDelete}><Trash2 size={17} /></button>
      </div>

      <div className="subtabs">
        {[["teams", "Teams"], ["schedule", "Schedule"], ["points", "Points"], ["leaders", "Leaders"]].map(([id, l]) => (
          <button key={id} className={cx("subtab", sub === id && "on")} onClick={() => setSub(id)}>{l}</button>
        ))}
      </div>

      {sub === "teams" && (
        <>
          {t.teams.length === 0 ? (
            <Empty icon={<Users size={26} />} title="No teams yet" sub={t.format === "doubles" ? "Register your pairs to get started." : "Register players to get started."} />
          ) : t.teams.map((tm) => (
            <div className="player-row" key={tm.id}>
              <div className="team-badge">{tm.name.slice(0, 1)}</div>
              <span className="player-row-name">{tm.name}</span>
              <button className="icon-btn" onClick={() => onChange({ ...t, teams: t.teams.filter((x) => x.id !== tm.id), matches: t.matches.filter((m) => m.aTeamId !== tm.id && m.bTeamId !== tm.id) })}><Trash2 size={16} /></button>
            </div>
          ))}
          <button className="btn btn-line btn-block" onClick={() => setAddTeam(true)}>
            <Plus size={17} /> {t.format === "doubles" ? "Add pair" : "Add team"}
          </button>
        </>
      )}

      {sub === "schedule" && (
        <>
          {t.matches.length === 0 ? (
            <Empty icon={<CalendarDays size={26} />} title="No matches scheduled" sub="Pick two teams to schedule a match." />
          ) : t.matches.map((m) => (
            <div className={cx("sched-row", m.result && "done")} key={m.id}>
              <div className="sched-main">
                <span className="sched-teams">{teamName(m.aTeamId)} <em>vs</em> {teamName(m.bTeamId)}</span>
                {m.result && <span className="sched-fmt">{teamName(m.result.winner === "A" ? m.aTeamId : m.bTeamId)} won · {Math.max(m.result.score.A, m.result.score.B)}–{Math.min(m.result.score.A, m.result.score.B)}</span>}
              </div>
              {m.result ? (
                <span className="done-check"><Check size={16} /></span>
              ) : (
                <button className="btn btn-primary btn-sm" onClick={() => playMatch(m)}><Play size={14} /> Play</button>
              )}
              <button className="icon-btn" onClick={() => onChange({ ...t, matches: t.matches.filter((x) => x.id !== m.id) })}><X size={16} /></button>
            </div>
          ))}
          <button className="btn btn-line btn-block" onClick={() => setAddMatch(true)} disabled={t.teams.length < 2}>
            <Plus size={17} /> Schedule match
          </button>
          {t.teams.length < 2 && <p className="foot-note">Register at least two teams first.</p>}
        </>
      )}

      {sub === "points" && (
        <div className="table">
          <div className="table-head"><span>Team</span><span>W</span><span>L</span><span>+/−</span><span>Pts</span></div>
          {standings.length === 0 ? <p className="foot-note">No results yet.</p> : standings.map((s, i) => (
            <div className="table-row" key={s.id}>
              <span className="t-name">{i === 0 && s.pts > 0 && <Medal size={13} className="lead-medal" />}{s.name}</span>
              <span>{s.w}</span><span>{s.l}</span><span>{s.pf - s.pa >= 0 ? "+" : ""}{s.pf - s.pa}</span>
              <span className="t-pts">{s.pts}</span>
            </div>
          ))}
        </div>
      )}

      {sub === "leaders" && (
        <div className="table">
          <div className="table-head lead"><span>Serve leaderboard</span><span>Pts</span></div>
          {leaderRows.length === 0 ? <p className="foot-note">Play a match to build the leaderboard.</p> : leaderRows.map((r, i) => (
            <div className="table-row lead" key={i}>
              <span className="t-name">{i < 3 && <Medal size={13} className={cx("lead-medal", `m${i}`)} />}{r.name}</span>
              <span className="t-pts">{r.pts}</span>
            </div>
          ))}
        </div>
      )}

      <TeamAdd open={addTeam} onClose={() => setAddTeam(false)} isPair={t.format === "doubles"} players={players}
        onAdd={(team) => { onChange({ ...t, teams: [...t.teams, team] }); setAddTeam(false); }} />
      <MatchAdd open={addMatch} onClose={() => setAddMatch(false)} teams={t.teams}
        onAdd={(aId, bId) => { onChange({ ...t, matches: [...t.matches, { id: uid(), aTeamId: aId, bTeamId: bId, result: null }] }); setAddMatch(false); }} />
    </div>
  );
}

function TeamAdd({ open, onClose, isPair, players, onAdd }) {
  const count = isPair ? 2 : 1;
  const [slots, setSlots] = useState([null, null]);
  const [name, setName] = useState("");
  const add = () => {
    const need = count;
    const p = slots.slice(0, need).filter(Boolean);
    if (p.length < need) return;
    const auto = p.map((x) => x.name.split(" ")[0]).join(" & ");
    onAdd({ id: uid(), name: name.trim() || auto, players: p });
    setSlots([null, null]); setName("");
  };
  return (
    <Sheet open={open} onClose={onClose} title={isPair ? "Add pair" : "Add team"}>
      <TeamSlots teamKey="A" label={isPair ? "Pair" : "Player"} count={count} values={slots} onChange={setSlots} players={players} />
      <div style={{ marginTop: 12 }} className="tiny-label">Team name (optional)</div>
      <input className="input" placeholder="Auto from player names" value={name} onChange={(e) => setName(e.target.value)} />
      <div className="sheet-btns">
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={add}>Save</button>
      </div>
    </Sheet>
  );
}

function MatchAdd({ open, onClose, teams, onAdd }) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  useEffect(() => { if (open && teams.length >= 2) { setA(teams[0].id); setB(teams[1].id); } }, [open]); // eslint-disable-line
  return (
    <Sheet open={open} onClose={onClose} title="Schedule a match">
      <div className="tiny-label">Team A</div>
      <select className="input" value={a} onChange={(e) => setA(e.target.value)}>
        {teams.map((tm) => <option key={tm.id} value={tm.id}>{tm.name}</option>)}
      </select>
      <div className="vs-mid">vs</div>
      <div className="tiny-label">Team B</div>
      <select className="input" value={b} onChange={(e) => setB(e.target.value)}>
        {teams.map((tm) => <option key={tm.id} value={tm.id}>{tm.name}</option>)}
      </select>
      <div className="sheet-btns">
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={() => a !== b && onAdd(a, b)} disabled={a === b}>Add match</button>
      </div>
      {a === b && <p className="foot-note">Pick two different teams.</p>}
    </Sheet>
  );
}

/* ============================================================
   HISTORY tab
   ============================================================ */
function HistoryTab({ history, setHistory }) {
  const [open, setOpen] = useState(null);
  const fmtDate = (ts) => new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  return (
    <div className="stack">
      {history.length === 0 ? (
        <Empty icon={<Clock size={30} />} title="No games yet" sub="Finished games show up here." />
      ) : history.map((g) => {
        const hi = Math.max(g.score.A, g.score.B), lo = Math.min(g.score.A, g.score.B);
        return (
          <button className="hist-row" key={g.id} onClick={() => setOpen(g)}>
            <div className="hist-main">
              <span className="hist-teams">{g.teams.A.name} <em>vs</em> {g.teams.B.name}</span>
              <span className="hist-meta">{fmtDate(g.endedAt)} · {g.format === "doubles" ? "Doubles" : "Singles"}</span>
            </div>
            <div className="hist-score">
              <span className="hist-num">{hi}–{lo}</span>
              <span className="hist-win">{g.endedEarly ? "ended early" : (g.winner === "A" ? g.teams.A.name : g.teams.B.name)}</span>
            </div>
          </button>
        );
      })}
      <Sheet open={!!open} onClose={() => setOpen(null)} title="Game details">
        {open && (
          <div className="hist-detail">
            <div className="hd-score">
              <div className="hd-team"><span>{open.teams.A.name}</span><b>{open.score.A}</b></div>
              <span className="hd-dash">–</span>
              <div className="hd-team"><b>{open.score.B}</b><span>{open.teams.B.name}</span></div>
            </div>
            <p className="hd-result">{open.endedEarly ? "Ended early" : `${open.winner === "A" ? open.teams.A.name : open.teams.B.name} won`} · played to {open.target}</p>
            {Object.keys(open.servePts || {}).length > 0 && (
              <>
                <div className="tiny-label" style={{ marginTop: 14 }}>Serve points</div>
                {Object.entries(open.servePts).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
                  <div className="won-leader-row" key={k}><span>{open.serveNames[k]}</span><b>{v}</b></div>
                ))}
              </>
            )}
            <button className="btn btn-line btn-block danger" style={{ marginTop: 16 }}
              onClick={() => { setHistory((h) => h.filter((x) => x.id !== open.id)); setOpen(null); }}>
              <Trash2 size={15} /> Delete this game
            </button>
          </div>
        )}
      </Sheet>
    </div>
  );
}

/* ============================================================
   STYLES
   ============================================================ */
const CSS = `
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800;900&family=Hanken+Grotesk:wght@400;500;600;700&display=swap');

:root{
  --court:#0E4D45; --court-2:#0A3B35; --court-line:rgba(230,243,239,.5);
  --ball:#CDFB45; --ball-deep:#8FB800;
  --ink:#0F211E; --muted:#66766F; --line:#E5E8E1;
  --paper:#F5F6F1; --card:#FFFFFF;
  --A:#0E7C6B; --B:#E4703A;
  --font-d:'Archivo',system-ui,sans-serif; --font-b:'Hanken Grotesk',system-ui,sans-serif;
  --shadow:0 1px 2px rgba(15,33,30,.05),0 6px 20px rgba(15,33,30,.06);
}
*{box-sizing:border-box}
.kp-root{font-family:var(--font-b);color:var(--ink);background:
  radial-gradient(120% 60% at 50% 0%, #163d38 0%, #0c2a26 55%, #081f1c 100%);
  min-height:100vh;min-height:100dvh;display:flex;justify-content:center;-webkit-font-smoothing:antialiased}
.kp-root *{margin:0}

.app{width:100%;max-width:468px;min-height:100vh;min-height:100dvh;background:var(--paper);
  position:relative;display:flex;flex-direction:column;
  box-shadow:0 0 60px rgba(0,0,0,.35)}

/* splash */
.splash{margin:auto;text-align:center;color:#eafbe8;display:flex;flex-direction:column;align-items:center;gap:14px}
.splash p{font-family:var(--font-d);font-weight:800;font-size:22px;letter-spacing:-.02em}

.ball-glow{filter:drop-shadow(0 0 8px rgba(205,251,69,.55))}

/* ---------- landing ---------- */
.landing{width:100%;max-width:468px;min-height:100vh;min-height:100dvh;position:relative;
  background:linear-gradient(165deg,#0E4D45,#0A342F 70%);color:#eafbe8;overflow:hidden;
  display:flex;flex-direction:column;justify-content:flex-end}
.landing-court{position:absolute;inset:0;opacity:.5}
.court-line{position:absolute;background:var(--court-line)}
.court-net{left:50%;top:0;bottom:0;width:2px;transform:translateX(-50%)}
.court-kitchen-top{left:0;right:0;top:38%;height:2px}
.court-kitchen-bot{left:0;right:0;top:62%;height:2px}
.landing-inner{position:relative;padding:32px 26px 40px;background:linear-gradient(180deg,transparent,rgba(8,31,28,.6) 30%)}
.brand-big{display:flex;align-items:center;gap:12px;font-family:var(--font-d);font-weight:900;
  font-size:34px;letter-spacing:-.03em;margin-bottom:16px}
.landing-lede{font-size:16px;line-height:1.5;color:#c7e6df;margin-bottom:26px;max-width:34ch}
.landing-actions{display:flex;flex-direction:column;gap:10px}
.note{margin-top:18px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);
  border-radius:14px;padding:14px 15px;font-size:13.5px;line-height:1.45;color:#dcefe9;
  display:flex;flex-direction:column;gap:11px}
.build{margin-top:22px;font-size:11px;color:rgba(234,251,232,.4);letter-spacing:.04em}

/* ---------- buttons ---------- */
.btn{font-family:var(--font-b);font-weight:600;font-size:15px;border:none;border-radius:13px;
  padding:13px 18px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:8px;
  transition:transform .08s ease,filter .15s ease,background .15s ease;line-height:1}
.btn:active{transform:scale(.975)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{background:var(--ball);color:#243a00;box-shadow:0 2px 0 var(--ball-deep)}
.btn-primary:hover{filter:brightness(1.03)}
.btn-primary.danger-fill{background:#E4703A;color:#fff;box-shadow:0 2px 0 #b8511f}
.btn-ghost{background:rgba(14,77,69,.08);color:var(--court)}
.btn-line{background:var(--card);color:var(--ink);border:1.5px solid var(--line)}
.btn-line.danger{color:#c0453b;border-color:#f0d3ce}
.btn-block{width:100%}
.btn-lg{padding:16px;font-size:16.5px;font-weight:700}
.btn-sm{padding:9px 13px;font-size:13.5px;border-radius:10px}
.landing .btn-line{background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.2);color:#eafbe8}
.landing .btn-ghost{background:rgba(255,255,255,.06);color:#eafbe8}

.icon-btn{background:transparent;border:none;color:var(--muted);cursor:pointer;padding:7px;
  border-radius:9px;display:inline-flex;transition:background .15s,color .15s}
.icon-btn:hover{background:rgba(15,33,30,.06);color:var(--ink)}
.icon-btn.sm{padding:4px}

/* ---------- topbar / tabbar ---------- */
.topbar{padding:16px 20px 8px;position:sticky;top:0;z-index:5;background:var(--paper)}
.brand{display:flex;align-items:center;gap:8px;font-family:var(--font-d);font-weight:800;
  font-size:15px;letter-spacing:-.01em;color:var(--court)}
.topbar-title{font-family:var(--font-d);font-weight:900;font-size:30px;letter-spacing:-.03em;margin-top:6px}
.content{flex:1;padding:6px 18px 96px;overflow-y:auto}
.tabbar{position:sticky;bottom:0;background:rgba(255,255,255,.92);backdrop-filter:blur(14px);
  border-top:1px solid var(--line);display:flex;padding:8px 6px calc(8px + env(safe-area-inset-bottom));
  z-index:10}
.tab{flex:1;background:none;border:none;cursor:pointer;display:flex;flex-direction:column;align-items:center;
  gap:3px;padding:6px 2px;color:var(--muted);font-family:var(--font-b);font-weight:600;font-size:10.5px;
  border-radius:12px;transition:color .15s}
.tab-on{color:var(--court)}
.tab-on span{color:var(--court)}

/* ---------- generic layout ---------- */
.stack{display:flex;flex-direction:column;gap:12px}
.card{background:var(--card);border-radius:18px;padding:16px;box-shadow:var(--shadow)}
.card-title{display:flex;align-items:center;gap:7px;font-family:var(--font-d);font-weight:700;font-size:15px}
.card-sub{color:var(--muted);font-size:13px;line-height:1.45;margin-top:5px}
.section-lede{color:var(--muted);font-size:14px;line-height:1.5;padding:2px 2px 4px}
.tiny-label{font-family:var(--font-d);font-weight:700;font-size:11px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted)}
.tiny-label.center{text-align:center}
.row-between{display:flex;align-items:center;justify-content:space-between;gap:12px}
.divider{height:1px;background:var(--line);margin:14px 0}
.foot-note{color:var(--muted);font-size:12.5px;line-height:1.4;padding:2px 4px;text-align:center}
.err{background:#fdece7;color:#b8431f;border-radius:11px;padding:11px 14px;font-size:13.5px;font-weight:500}

/* ---------- seg control ---------- */
.seg{display:flex;background:#eef0ea;border-radius:12px;padding:3px;gap:3px;margin-top:8px}
.seg-sm{margin-top:0}
.seg-btn{flex:1;border:none;background:transparent;font-family:var(--font-d);font-weight:700;
  font-size:14px;padding:9px 6px;border-radius:9px;cursor:pointer;color:var(--muted);transition:all .15s}
.seg-sm .seg-btn{padding:7px 12px;font-size:13px;flex:0 1 auto}
.seg-on{background:var(--card);color:var(--court);box-shadow:0 1px 3px rgba(15,33,30,.12)}

/* ---------- toggle ---------- */
.toggle-row{display:flex;align-items:center;justify-content:space-between;gap:14px;width:100%;
  background:none;border:none;cursor:pointer;padding:0;text-align:left}
.toggle-text{display:flex;flex-direction:column;gap:3px}
.toggle-label{font-family:var(--font-d);font-weight:700;font-size:15px;color:var(--ink)}
.toggle-hint{font-size:12.5px;color:var(--muted);line-height:1.35;max-width:30ch}
.switch{width:46px;height:28px;background:#d5d9d1;border-radius:99px;position:relative;flex:none;transition:background .2s}
.switch-on{background:var(--court)}
.knob{position:absolute;top:3px;left:3px;width:22px;height:22px;background:#fff;border-radius:99px;
  transition:transform .2s;box-shadow:0 1px 2px rgba(0,0,0,.2)}
.switch-on .knob{transform:translateX(18px)}

/* ---------- team slots ---------- */
.team-card{background:var(--card);border-radius:18px;padding:15px;box-shadow:var(--shadow)}
.team-card-head{display:flex;align-items:center;gap:8px;font-family:var(--font-d);font-weight:800;font-size:15px;margin-bottom:11px}
.team-dot{width:10px;height:10px;border-radius:99px}
.team-dot[data-team=A]{background:var(--A)} .team-dot[data-team=B]{background:var(--B)}
.slots{display:flex;flex-direction:column;gap:8px}
.slot{display:flex;align-items:center;gap:10px;border-radius:12px;padding:9px 11px;font-family:var(--font-b);font-size:14.5px}
.slot-filled{background:#f2f4ef}
.slot-name{flex:1;font-weight:600}
.slot-add{background:transparent;border:1.5px dashed var(--line);color:var(--muted);cursor:pointer;
  font-weight:600;justify-content:center;transition:border-color .15s,color .15s}
.slot-add:hover{border-color:var(--court);color:var(--court)}

/* ---------- avatar ---------- */
.avatar{border-radius:99px;object-fit:cover;display:flex;align-items:center;justify-content:center;
  flex:none;overflow:hidden;background:#e6e9e3}
.avatar-empty{background:#e6e9e3;color:#9aa89f;font-weight:700}

/* ---------- coin toss ---------- */
.toss-stage{display:flex;justify-content:center;padding:16px 0 6px}
.coin{display:flex}
.coin-flip{animation:flip .9s ease-in-out}
@keyframes flip{0%{transform:rotateY(0) translateY(0)}50%{transform:rotateY(900deg) translateY(-30px)}100%{transform:rotateY(1800deg) translateY(0)}}
.btn-block+.toss-result{margin-top:12px}
.toss-result{margin-top:12px;text-align:center;font-size:14px}
.toss-choices{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.firstserve{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:14px;
  padding-top:14px;border-top:1px solid var(--line)}

/* ---------- LIVE ---------- */
.live{padding:14px 16px calc(20px + env(safe-area-inset-bottom));display:flex;flex-direction:column;gap:14px;flex:1}
.live-head{display:flex;align-items:center;gap:8px;font-family:var(--font-d);font-weight:700;font-size:13px;
  color:var(--muted);text-transform:uppercase;letter-spacing:.06em;padding-top:4px}
.live-dot{width:8px;height:8px;border-radius:99px;background:#e4703a;animation:blink 1.4s infinite}
@keyframes blink{50%{opacity:.3}}
.live-target{margin-left:auto;text-transform:none;letter-spacing:0;color:var(--muted);font-weight:600;font-size:12px}

.scoreboard{background:linear-gradient(160deg,var(--court),var(--court-2));border-radius:22px;
  padding:18px 16px;position:relative;overflow:hidden;color:#eafbe8;box-shadow:0 10px 30px rgba(10,52,47,.4)}
.court-bg{position:absolute;inset:0;opacity:.5;pointer-events:none}
.cb-line{position:absolute;background:rgba(230,243,239,.28)}
.cb-net{left:0;right:0;top:50%;height:2px}
.cb-k1{left:0;right:0;top:34%;height:1.5px} .cb-k2{left:0;right:0;top:66%;height:1.5px}

.score-team{position:relative;padding:10px 6px;border-radius:14px;transition:background .2s}
.score-team.serving{background:rgba(205,251,69,.09)}
.score-team.pulsing .score-num{animation:pop .36s ease}
@keyframes pop{0%{transform:scale(1)}40%{transform:scale(1.13)}100%{transform:scale(1)}}
.score-team-top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:2px}
.score-team-players{display:flex;flex-wrap:wrap;gap:6px}
.pl-chip{display:flex;align-items:center;gap:6px;background:rgba(255,255,255,.08);border-radius:99px;
  padding:4px 10px 4px 4px;font-size:13px;font-weight:600;position:relative}
.pl-serving{background:rgba(205,251,69,.16);box-shadow:inset 0 0 0 1.5px rgba(205,251,69,.4)}
.srv-ball{display:flex;margin-left:1px}
.swap-btn{background:rgba(255,255,255,.09);border:none;color:#cfe8e1;font-family:var(--font-b);
  font-weight:600;font-size:11.5px;border-radius:8px;padding:5px 8px;cursor:pointer;display:flex;
  align-items:center;gap:4px;flex:none}
.swap-btn:hover{background:rgba(255,255,255,.16)}
.score-num{font-family:var(--font-d);font-weight:900;font-size:76px;line-height:.9;letter-spacing:-.04em;
  font-variant-numeric:tabular-nums}
.score-team[data-team=A] .score-num{color:#8ff0dc}
.score-team[data-team=B] .score-num{color:#ffc08e}
.serve-tag{font-size:12px;color:#bfe3db;font-weight:600;margin-top:4px}

.score-mid{display:flex;align-items:center;justify-content:center;gap:14px;padding:8px 0;
  border-top:1px solid rgba(230,243,239,.14);border-bottom:1px solid rgba(230,243,239,.14);margin:4px 0}
.call{font-family:var(--font-d);font-weight:800;font-size:22px;letter-spacing:.02em;
  font-variant-numeric:tabular-nums;color:var(--ball)}
.serve-diagram{display:flex}
.sd-half{display:flex;gap:3px}
.sd-cell{width:16px;height:22px;border-radius:3px;background:rgba(255,255,255,.12)}
.sd-active{background:var(--ball);box-shadow:0 0 8px rgba(205,251,69,.6)}

.serving-banner{display:flex;align-items:center;gap:9px;background:var(--card);border-radius:14px;
  padding:12px 14px;font-size:14px;box-shadow:var(--shadow)}
.serving-banner b{font-weight:700}

.result-btns{display:flex;gap:11px}
.btn-result{flex:1;border:none;border-radius:18px;padding:20px 12px;cursor:pointer;display:flex;
  flex-direction:column;align-items:center;gap:4px;font-family:var(--font-b);transition:transform .08s,filter .15s}
.btn-result:active{transform:scale(.97)}
.br-big{font-family:var(--font-d);font-weight:800;font-size:22px}
.br-sub{font-size:11.5px;opacity:.85;font-weight:500}
.btn-point{background:var(--ball);color:#243a00;box-shadow:0 3px 0 var(--ball-deep)}
.btn-sideout{background:var(--card);color:var(--ink);border:1.5px solid var(--line);box-shadow:0 3px 0 #e5e8e1}

.live-actions{display:flex;gap:10px}
.live-actions .btn{flex:1}

/* ---------- won ---------- */
.won{padding:40px 26px;text-align:center;display:flex;flex-direction:column;align-items:center;flex:1;justify-content:center}
.won-confetti{margin-bottom:18px}
.won-eyebrow{font-family:var(--font-d);font-weight:700;font-size:12px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted)}
.won-team{font-family:var(--font-d);font-weight:900;font-size:34px;letter-spacing:-.03em;margin:6px 0 4px}
.won-score{font-family:var(--font-d);font-weight:900;font-size:60px;letter-spacing:-.04em;color:var(--court);
  font-variant-numeric:tabular-nums;line-height:1}
.won-leaders{width:100%;max-width:280px;margin:26px 0}
.won-leader-row{display:flex;justify-content:space-between;padding:9px 2px;border-bottom:1px solid var(--line);font-size:14.5px}
.won-leader-row b{font-family:var(--font-d);color:var(--court)}
.won .btn{width:100%;max-width:320px;margin-top:8px}

/* ---------- players / rows ---------- */
.players-list,.stack>.player-row{display:flex}
.players-list{flex-direction:column;gap:9px}
.player-row{display:flex;align-items:center;gap:12px;background:var(--card);border-radius:15px;
  padding:11px 13px;box-shadow:var(--shadow)}
.player-row-name{flex:1;font-weight:600;font-size:15.5px}
.team-badge{width:44px;height:44px;border-radius:12px;background:var(--court);color:#eafbe8;
  display:flex;align-items:center;justify-content:center;font-family:var(--font-d);font-weight:800;font-size:19px;flex:none}

/* ---------- empty ---------- */
.empty{text-align:center;padding:44px 20px;color:var(--muted)}
.empty-ico{width:64px;height:64px;border-radius:20px;background:#eaeee8;display:flex;align-items:center;
  justify-content:center;margin:0 auto 14px;color:var(--court)}
.empty-title{font-family:var(--font-d);font-weight:800;font-size:17px;color:var(--ink)}
.empty-sub{font-size:13.5px;margin-top:4px;line-height:1.4;max-width:26ch;margin-inline:auto}

/* ---------- sheet ---------- */
.sheet-scrim{position:fixed;inset:0;background:rgba(10,25,22,.5);z-index:50;display:flex;align-items:flex-end;
  justify-content:center;animation:fade .2s ease}
@keyframes fade{from{opacity:0}}
.sheet{background:var(--paper);width:100%;max-width:468px;border-radius:24px 24px 0 0;padding:8px 20px calc(24px + env(safe-area-inset-bottom));
  max-height:88vh;overflow-y:auto;animation:slideup .28s cubic-bezier(.2,.9,.3,1)}
@keyframes slideup{from{transform:translateY(100%)}}
.sheet-grip{width:38px;height:4px;background:#d3d7cf;border-radius:99px;margin:8px auto 6px}
.sheet-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.sheet-head h3{font-family:var(--font-d);font-weight:800;font-size:19px;letter-spacing:-.01em}
.sheet-text{color:var(--muted);font-size:14px;line-height:1.5;margin-bottom:14px}
.sheet-btns{display:flex;gap:10px;margin-top:18px}
.sheet-btns .btn{flex:1}

.input{width:100%;background:var(--card);border:1.5px solid var(--line);border-radius:12px;
  padding:12px 14px;font-family:var(--font-b);font-size:15px;color:var(--ink);outline:none;transition:border-color .15s}
.input:focus{border-color:var(--court)}
select.input{appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2366766f' stroke-width='3'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 14px center}

/* pick sheet */
.pick-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}
.pick-cell{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:11px 4px;
  display:flex;flex-direction:column;align-items:center;gap:6px;cursor:pointer;font-size:11.5px;font-weight:600;
  transition:border-color .15s,transform .08s}
.pick-cell:hover{border-color:var(--court)}
.pick-cell:active{transform:scale(.96)}
.pick-cell span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.pick-adhoc{display:flex;gap:8px}
.pick-adhoc .input{flex:1}

/* editor */
.editor{display:flex;flex-direction:column;gap:12px}
.editor-avatar{display:flex;justify-content:center;padding:4px 0}
.editor-photo-row{display:flex;gap:8px;flex-wrap:wrap}
.emoji-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}
.emoji-cell{aspect-ratio:1;background:var(--card);border:1.5px solid var(--line);border-radius:12px;
  font-size:22px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .12s}
.emoji-cell:hover{border-color:var(--court)}
.emoji-cell.on{border-color:var(--court);background:rgba(14,77,69,.08);transform:scale(1.04)}

/* schedule */
.sched-row{display:flex;align-items:center;gap:10px;background:var(--card);border-radius:15px;
  padding:12px 13px;box-shadow:var(--shadow)}
.sched-row.done{opacity:.72}
.sched-main{flex:1;display:flex;flex-direction:column;gap:3px;min-width:0}
.sched-fmt{font-size:11.5px;color:var(--muted);font-weight:600}
.sched-teams{font-weight:600;font-size:14.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sched-teams em{color:var(--muted);font-style:normal;font-weight:500;font-size:12px;padding:0 2px}
.done-check{color:var(--court);display:flex}

/* profile chip */
.profile-chip{display:flex;align-items:center;gap:12px;background:var(--card);border:none;border-radius:16px;
  padding:12px 14px;box-shadow:var(--shadow);cursor:pointer;text-align:left;width:100%}
.profile-ava{width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,var(--court),#0a342f);
  color:#eafbe8;display:flex;align-items:center;justify-content:center;font-family:var(--font-d);font-weight:800;font-size:18px;flex:none}
.profile-txt{flex:1;display:flex;flex-direction:column;gap:2px;min-width:0}
.profile-txt span{font-weight:700;font-size:15px}
.profile-txt em{font-style:normal;font-size:11.5px;color:var(--muted);line-height:1.3}

/* tourney */
.tourney-row{display:flex;align-items:center;gap:12px;background:var(--card);border:none;border-radius:16px;
  padding:15px 14px;box-shadow:var(--shadow);cursor:pointer;width:100%;text-align:left;color:var(--muted)}
.tourney-main{flex:1;display:flex;flex-direction:column;gap:3px}
.tourney-name{font-family:var(--font-d);font-weight:800;font-size:16.5px;color:var(--ink);letter-spacing:-.01em}
.tourney-meta{font-size:12.5px;color:var(--muted)}

.detail-head{display:flex;align-items:center;gap:8px}
.detail-title{flex:1;min-width:0}
.detail-title h2{font-family:var(--font-d);font-weight:900;font-size:22px;letter-spacing:-.02em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.detail-title span{font-size:12px;color:var(--muted)}
.subtabs{display:flex;gap:6px;background:#eef0ea;border-radius:12px;padding:4px}
.subtab{flex:1;border:none;background:transparent;font-family:var(--font-d);font-weight:700;font-size:13px;
  padding:9px 4px;border-radius:9px;cursor:pointer;color:var(--muted);transition:all .15s}
.subtab.on{background:var(--card);color:var(--court);box-shadow:0 1px 3px rgba(15,33,30,.12)}

.date-row{display:flex;align-items:center;gap:8px}
.date-row .input{flex:1}
.date-arrow{color:var(--muted)}
.vs-mid{text-align:center;font-family:var(--font-d);font-weight:800;color:var(--muted);margin:8px 0}

/* tables */
.table{background:var(--card);border-radius:16px;padding:6px 4px;box-shadow:var(--shadow);overflow:hidden}
.table-head{display:grid;grid-template-columns:1fr 32px 32px 44px 40px;gap:6px;padding:10px 12px;
  font-family:var(--font-d);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted);align-items:center}
.table-head span:not(:first-child){text-align:center}
.table-head.lead{grid-template-columns:1fr 44px}
.table-row{display:grid;grid-template-columns:1fr 32px 32px 44px 40px;gap:6px;padding:12px 12px;
  border-top:1px solid var(--line);font-size:14px;align-items:center;font-variant-numeric:tabular-nums}
.table-row span:not(.t-name){text-align:center}
.table-row.lead{grid-template-columns:1fr 44px}
.t-name{font-weight:600;display:flex;align-items:center;gap:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t-pts{font-family:var(--font-d);font-weight:800;color:var(--court)}
.lead-medal{color:#c9a227} .lead-medal.m0{color:#e0b000} .lead-medal.m1{color:#9aa5ad} .lead-medal.m2{color:#c08457}

/* history */
.hist-row{display:flex;align-items:center;gap:12px;background:var(--card);border:none;border-radius:15px;
  padding:13px 14px;box-shadow:var(--shadow);cursor:pointer;width:100%;text-align:left}
.hist-main{flex:1;display:flex;flex-direction:column;gap:3px;min-width:0}
.hist-teams{font-weight:600;font-size:14.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hist-teams em{color:var(--muted);font-style:normal;font-weight:500;font-size:12px;padding:0 2px}
.hist-meta{font-size:11.5px;color:var(--muted)}
.hist-score{text-align:right;display:flex;flex-direction:column;gap:1px;flex:none}
.hist-num{font-family:var(--font-d);font-weight:800;font-size:19px;font-variant-numeric:tabular-nums;color:var(--court)}
.hist-win{font-size:10.5px;color:var(--muted);max-width:90px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hist-detail{padding-bottom:4px}
.hd-score{display:flex;align-items:center;justify-content:center;gap:14px;padding:10px 0 6px}
.hd-team{display:flex;flex-direction:column;align-items:center;gap:4px;flex:1}
.hd-team span{font-size:12.5px;color:var(--muted);font-weight:600;text-align:center}
.hd-team b{font-family:var(--font-d);font-weight:900;font-size:44px;color:var(--court);line-height:1}
.hd-dash{font-family:var(--font-d);font-weight:800;font-size:26px;color:var(--muted)}
.hd-result{text-align:center;font-size:13.5px;color:var(--muted);margin-top:8px}

@media (prefers-reduced-motion: reduce){
  *{animation:none!important;transition:none!important}
}
`;
