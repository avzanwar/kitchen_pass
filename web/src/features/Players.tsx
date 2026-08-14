import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2, Users } from "lucide-react";
import { useEffect, useState } from "react";

import { AvatarChip, EMOJIS, Empty, ErrorNote, PALETTE, Sheet, Spinner, cx } from "../components/ui";
import { api, type Player } from "../lib/api";

export default function Players() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Partial<Player> | null>(null);
  const list = useQuery({ queryKey: ["players"], queryFn: () => api.players() });

  const remove = useMutation({
    mutationFn: (id: string) => api.deletePlayer(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["players"] }),
  });

  if (list.isLoading) return <Spinner />;

  return (
    <div className="stack">
      <p className="section-lede">
        Save your regulars once, then pick them by name anywhere you register a team.
      </p>
      <ErrorNote error={list.error} />
      <ErrorNote error={remove.error} />

      {list.data?.length === 0 ? (
        <Empty icon={<Users size={30} />} title="No players yet"
          sub="Add the people you play with most." />
      ) : (
        <div className="players-list">
          {list.data?.map((p) => (
            <div className="player-row" key={p.id}>
              <AvatarChip player={p} size={44} />
              <span className="player-row-name">
                {p.name}
                {p.rating ? <em className="rating-tag">{p.rating.toFixed(1)}</em> : null}
              </span>
              <button className="icon-btn" onClick={() => setEditing(p)} aria-label="Edit">
                <Pencil size={17} />
              </button>
              <button className="icon-btn" onClick={() => remove.mutate(p.id)} aria-label="Delete">
                <Trash2 size={17} />
              </button>
            </div>
          ))}
        </div>
      )}

      <button className="btn btn-primary btn-block" onClick={() => setEditing({})}>
        <Plus size={18} /> Add player
      </button>

      <PlayerEditor player={editing} onClose={() => setEditing(null)} />
    </div>
  );
}

function PlayerEditor({ player, onClose }: { player: Partial<Player> | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [rating, setRating] = useState("");
  const [avatar, setAvatar] = useState<Player["avatar"]>(null);

  useEffect(() => {
    if (player) {
      setName(player.name ?? "");
      setRating(player.rating ? String(player.rating) : "");
      setAvatar(player.avatar ?? null);
    }
  }, [player]);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        rating: rating ? Number(rating) : null,
        avatar: avatar ?? {
          type: "initials",
          color: PALETTE[name.trim().length % PALETTE.length],
        },
      };
      return player?.id ? api.updatePlayer(player.id, body) : api.createPlayer(body);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["players"] });
      onClose();
    },
  });

  return (
    <Sheet open={player !== null} onClose={onClose}
      title={player?.id ? "Edit player" : "Add player"}>
      <div className="editor">
        <div className="editor-avatar">
          <AvatarChip player={{ name: name || "?", avatar }} size={84} />
        </div>
        <input className="input" placeholder="Name" value={name} autoFocus
          onChange={(e) => setName(e.target.value)} />
        <div className="tiny-label" style={{ marginTop: 10 }}>Rating (optional)</div>
        <input className="input" type="number" step="0.25" min="0" max="8"
          placeholder="e.g. 4.0" value={rating}
          onChange={(e) => setRating(e.target.value)} />

        <div className="tiny-label" style={{ marginTop: 12 }}>Pick an emoji</div>
        <div className="emoji-grid">
          {EMOJIS.map((e, i) => (
            <button
              key={e}
              className={cx("emoji-cell", avatar?.type === "emoji" && avatar.value === e && "on")}
              onClick={() => setAvatar({
                type: "emoji", value: e, color: PALETTE[i % PALETTE.length],
              })}
            >
              {e}
            </button>
          ))}
        </div>
        <ErrorNote error={save.error} />
        <div className="sheet-btns">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={!name.trim() || save.isPending}
            onClick={() => save.mutate()}>
            Save
          </button>
        </div>
      </div>
    </Sheet>
  );
}
