/**
 * Presentational pieces carried over from kitchen-pass.jsx.
 *
 * These are near-verbatim ports — the prototype's design is good and the CSS
 * class names are unchanged, so the look survives the rewrite. Only the types
 * are new.
 */

import { X } from "lucide-react";
import type { ReactNode } from "react";

import type { Avatar } from "../lib/api";

export type { Avatar };

export const cx = (...parts: (string | false | null | undefined)[]): string =>
  parts.filter(Boolean).join(" ");

export const PALETTE = [
  "#0E7C6B", "#EA6D3A", "#3B7DC4", "#B4529E",
  "#D99A00", "#5B8A3A", "#C0453B", "#6D5BD0",
];

export const EMOJIS = ["🏓", "🎾", "🔥", "⭐", "🦅", "🐅", "🚀", "🦈", "⚡", "🌊", "🥇", "🧢"];

export interface PlayerLike {
  id?: string;
  name: string;
  avatar?: Avatar | null;
}

export function Ball({ size = 22, glow = false }: { size?: number; glow?: boolean }) {
  const holes: [number, number][] = [
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

export function AvatarChip({ player, size = 40 }: { player?: PlayerLike | null; size?: number }) {
  const style = { width: size, height: size, fontSize: size * 0.42 };
  if (!player) return <div className="avatar avatar-empty" style={style}>?</div>;
  if (player.avatar?.type === "photo" && player.avatar.value) {
    return <img className="avatar" style={style} src={player.avatar.value} alt="" />;
  }
  if (player.avatar?.type === "emoji") {
    return (
      <div className="avatar" style={{ ...style, background: player.avatar.color || "#e6e9e3" }}>
        {player.avatar.value}
      </div>
    );
  }
  const initial = (player.name || "?").trim().slice(0, 1).toUpperCase();
  return (
    <div
      className="avatar"
      style={{ ...style, background: player.avatar?.color || "#0E7C6B", color: "#fff", fontWeight: 700 }}
    >
      {initial}
    </div>
  );
}

export interface SegOption<T> {
  value: T;
  label: string;
}

export function Seg<T extends string | number>({
  options, value, onChange, small,
}: {
  options: SegOption<T>[];
  value: T;
  onChange: (value: T) => void;
  small?: boolean;
}) {
  return (
    <div className={cx("seg", small && "seg-sm")}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          className={cx("seg-btn", value === o.value && "seg-on")}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({
  on, onChange, label, hint,
}: {
  on: boolean;
  onChange: (on: boolean) => void;
  label: string;
  hint?: string;
}) {
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

export function Sheet({
  open, onClose, title, children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-grip" />
        <div className="sheet-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={20} />
          </button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );
}

export function Empty({ icon, title, sub }: { icon: ReactNode; title: string; sub: string }) {
  return (
    <div className="empty">
      <div className="empty-ico">{icon}</div>
      <p className="empty-title">{title}</p>
      <p className="empty-sub">{sub}</p>
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="splash">
      <Ball size={40} glow />
      <p>{label}</p>
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return <div className="err">{message}</div>;
}
