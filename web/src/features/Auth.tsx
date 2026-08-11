import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Ball } from "../components/ui";
import { api, setToken } from "../lib/api";

export default function Auth() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = mode === "login"
        ? await api.login(email, password)
        : await api.register(email, password, name);
      setToken(result.access_token);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

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
          Run pickleball tournaments end to end — draws, courts, rules-based
          scoring and live standings. Scoring keeps working when the wifi
          doesn't.
        </p>

        <form className="auth-form" onSubmit={submit}>
          {mode === "register" && (
            <input
              className="input" placeholder="Your name" value={name}
              onChange={(e) => setName(e.target.value)} autoComplete="name"
            />
          )}
          <input
            className="input" type="email" placeholder="Email" value={email} required
            onChange={(e) => setEmail(e.target.value)} autoComplete="email"
          />
          <input
            className="input" type="password" placeholder="Password" value={password}
            required minLength={8} onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
          {error && <div className="err">{error}</div>}
          <button className="btn btn-primary btn-block btn-lg" disabled={busy}>
            {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        <button
          className="btn btn-ghost"
          onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}
        >
          {mode === "login" ? "Create an account instead" : "I already have an account"}
        </button>
        <p className="foot-note center">
          Passwords need at least 8 characters.
        </p>
      </div>
    </div>
  );
}
