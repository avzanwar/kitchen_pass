import { useQuery } from "@tanstack/react-query";
import { Clock, LogOut, Trophy, Users, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { Ball, Spinner, cx } from "./components/ui";
import Auth from "./features/Auth";
import CasualPlay from "./features/CasualPlay";
import BulkImport from "./features/BulkImport";
import DivisionDetail from "./features/DivisionDetail";
import LiveMatch from "./features/LiveMatch";
import Players from "./features/Players";
import PublicView from "./features/PublicView";
import TournamentDetail from "./features/TournamentDetail";
import Tournaments from "./features/Tournaments";
import { api, clearToken, getToken } from "./lib/api";
import { pendingCount } from "./lib/outbox";

const TABS = [
  { to: "/", label: "Events", icon: Trophy },
  { to: "/play", label: "Play", icon: Zap },
  { to: "/players", label: "Players", icon: Users },
] as const;

function Shell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [pending, setPending] = useState(0);
  const [online, setOnline] = useState(navigator.onLine);

  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });

  useEffect(() => {
    const tick = async () => setPending(await pendingCount());
    void tick();
    const id = setInterval(tick, 3000);
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      clearInterval(id);
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  const signOut = () => {
    clearToken();
    navigate("/signin", { replace: true });
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><Ball size={22} /><span>Kitchen Pass</span></div>
        <div className="topbar-right">
          {!online && <span className="chip-offline">offline</span>}
          {pending > 0 && <span className="chip-pending">{pending} queued</span>}
          {me.data && (
            <button className="icon-btn" onClick={signOut} title="Sign out">
              <LogOut size={17} />
            </button>
          )}
        </div>
      </header>

      <main className="content">{children}</main>

      <nav className="tabbar">
        {TABS.map(({ to, label, icon: Icon }) => {
          const active = to === "/"
            ? location.pathname === "/" || location.pathname.startsWith("/tournaments")
            : location.pathname.startsWith(to);
          return (
            <Link key={to} to={to} className={cx("tab", active && "tab-on")}>
              <Icon size={21} strokeWidth={active ? 2.4 : 2} />
              <span>{label}</span>
            </Link>
          );
        })}
        <span className="tab tab-static">
          <Clock size={21} strokeWidth={2} />
          <span>{pending > 0 ? `${pending} queued` : "synced"}</span>
        </span>
      </nav>
    </div>
  );
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = getToken();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false, enabled: Boolean(token) });

  if (!token) return <Navigate to="/signin" replace />;
  if (me.isLoading) return <div className="app"><Spinner /></div>;
  if (me.isError) return <Navigate to="/signin" replace />;
  return <Shell>{children}</Shell>;
}

export default function App() {
  return (
    <div className="kp-root">
      <Routes>
        <Route path="/signin" element={<div className="app"><Auth /></div>} />
        <Route path="/live/:token" element={<div className="app"><main className="content"><PublicView /></main></div>} />
        <Route path="/matches/:matchId" element={
          <RequireAuth><LiveMatch /></RequireAuth>
        } />
        <Route path="/" element={<RequireAuth><Tournaments /></RequireAuth>} />
        <Route path="/play" element={<RequireAuth><CasualPlay /></RequireAuth>} />
        <Route path="/players" element={<RequireAuth><Players /></RequireAuth>} />
        <Route path="/import" element={<RequireAuth><BulkImport /></RequireAuth>} />
        <Route path="/tournaments/:tournamentId" element={
          <RequireAuth><TournamentDetail /></RequireAuth>
        } />
        <Route path="/divisions/:divisionId" element={
          <RequireAuth><DivisionDetail /></RequireAuth>
        } />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
