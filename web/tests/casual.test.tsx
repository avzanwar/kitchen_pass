// @vitest-environment jsdom
/**
 * The pickup-game setup screen.
 *
 * What matters here is what gets sent: a saved player must go as a
 * `player_id` and a typed name as a `name`, because that distinction is what
 * keeps two people called "Mike" apart on the server. The rest is the guard
 * that stops a half-filled team starting a game.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import CasualPlay from "../src/features/CasualPlay";

// Deliberately overlapping names: "Novak"/"Nova", "Mike"/"Mikaela", and two
// people whose first names both start with "Ni" — search is only interesting
// when some entries match and others do not.
const ROSTER = [
  { id: "p1", name: "Ivo Novak", avatar: null, rating: 4.25, is_guest: false },
  { id: "p2", name: "Priya Raman", avatar: null, rating: 4.0, is_guest: false },
  { id: "p3", name: "Nina Roth", avatar: null, rating: 3.5, is_guest: false },
  { id: "p4", name: "Nikhil Rao", avatar: null, rating: 3.75, is_guest: false },
  { id: "p5", name: "Sam Whitfield", avatar: null, rating: 4.0, is_guest: false },
];
const GUESTS = [
  ...ROSTER,
  { id: "g1", name: "Mike", avatar: null, rating: null, is_guest: true },
  { id: "g2", name: "Mikaela", avatar: null, rating: null, is_guest: true },
];

const CREATED = {
  match_id: "m1", division_id: "d1", status: "ready", format: "doubles",
  scoring: "sideout", target: 11, best_of: 1, created_at: "2026-08-14T10:00:00Z",
  a_name: "Ivo & Mike", b_name: "Priya & Dave",
  a_players: [], b_players: [], winner: null, games_won: {}, games: [],
};

/** Route every endpoint the screen touches. */
function stubFetch(overrides: Record<string, unknown> = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const handler = vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const body =
      url.includes("include_guests=true") ? GUESTS
      : url.includes("/players") ? ROSTER
      : url.includes("/casual/matches") && init?.method === "POST" ? CREATED
      : url.includes("/casual/matches") ? (overrides.casual ?? [])
      : null;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: init?.method === "POST" ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", handler);
  return calls;
}

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/play"]}>
        <Routes>
          <Route path="/play" element={<CasualPlay />} />
          <Route path="/matches/:id" element={<div>Scoring screen</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * Fill the next empty slot on a team, from the roster grid or by typing.
 *
 * Scoped to the team's own card rather than indexing the flat list of "Add
 * player" buttons — that list shrinks as slots fill, so a fixed index silently
 * starts pointing at the other team.
 */
async function fillSlot(teamIndex: number, pick: { player?: string; name?: string }) {
  const card = document.querySelectorAll(".team-card")[teamIndex] as HTMLElement;
  fireEvent.click(within(card).getAllByText("Add player")[0]);
  await waitFor(() => expect(screen.getByText("Choose a player")).toBeTruthy());

  if (pick.player) {
    await waitFor(() => expect(screen.getByText(pick.player!)).toBeTruthy());
    fireEvent.click(screen.getByText(pick.player));
  } else {
    const input = screen.getByPlaceholderText("Search, or type a new name");
    fireEvent.change(input, { target: { value: pick.name } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
  }
  await waitFor(() => expect(screen.queryByText("Choose a player")).toBeNull());
}

const bodyOf = (calls: { url: string; init?: RequestInit }[]) =>
  JSON.parse(
    (calls.find((c) => c.url.includes("/casual/matches") && c.init?.method === "POST")!
      .init!.body) as string,
  );

describe("pickup game setup", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("will not start until both teams are full", async () => {
    stubFetch();
    wrap();

    const start = screen.getByText("Start game").closest("button")!;
    expect(start.disabled).toBe(true);
    expect(screen.getByText(/Pick 2 players for each side/)).toBeTruthy();

    // Three of four slots filled is still not enough.
    await fillSlot(0, { player: "Ivo Novak" });
    await fillSlot(0, { name: "Mike" });
    await fillSlot(1, { player: "Priya Raman" });
    expect(screen.getByText("Start game").closest("button")!.disabled).toBe(true);
  });

  it("sends a saved player as an id and a typed name as a name", async () => {
    const calls = stubFetch();
    wrap();

    await fillSlot(0, { player: "Ivo Novak" });
    await fillSlot(0, { name: "Mike" });
    await fillSlot(1, { player: "Priya Raman" });
    await fillSlot(1, { name: "Dave" });

    const start = screen.getByText("Start game").closest("button")!;
    await waitFor(() => expect(start.disabled).toBe(false));
    fireEvent.click(start);

    await waitFor(() => expect(screen.getByText("Scoring screen")).toBeTruthy());
    const body = bodyOf(calls);
    // The distinction that keeps two Mikes apart on the server.
    expect(body.a.players).toEqual([{ player_id: "p1" }, { name: "Mike" }]);
    expect(body.b.players).toEqual([{ player_id: "p2" }, { name: "Dave" }]);
  });

  it("sends the settings the screen shows", async () => {
    const calls = stubFetch();
    wrap();

    fireEvent.click(screen.getByText("Rally"));
    fireEvent.click(screen.getByText("21"));
    fireEvent.click(screen.getByText("Best of 3"));
    fireEvent.click(screen.getByText("Win by 2"));

    await fillSlot(0, { name: "A1" });
    await fillSlot(0, { name: "A2" });
    await fillSlot(1, { name: "B1" });
    await fillSlot(1, { name: "B2" });
    fireEvent.click(screen.getByText("Start game"));

    await waitFor(() => expect(screen.getByText("Scoring screen")).toBeTruthy());
    const body = bodyOf(calls);
    expect(body.scoring).toBe("rally");
    expect(body.target).toBe(21);
    expect(body.best_of).toBe(3);
    expect(body.win_by_2).toBe(false);
  });

  it("singles needs one player a side", async () => {
    const calls = stubFetch();
    wrap();

    fireEvent.click(screen.getByText("Singles"));
    expect(screen.getAllByText("Add player")).toHaveLength(2);
    expect(screen.getByText(/Pick 1 player for each side/)).toBeTruthy();

    await fillSlot(0, { player: "Ivo Novak" });
    await fillSlot(1, { player: "Priya Raman" });

    fireEvent.click(screen.getByText("Start game"));
    await waitFor(() => expect(screen.getByText("Scoring screen")).toBeTruthy());
    const body = bodyOf(calls);
    expect(body.format).toBe("singles");
    expect(body.a.players).toEqual([{ player_id: "p1" }]);
  });

  it("the coin toss picks who serves first", async () => {
    const calls = stubFetch();
    wrap();

    // The Seg under "Serving first" is the deterministic way in; the flip
    // itself is random and lands on the same control.
    const firstServe = screen.getByText("Serving first").closest(".firstserve")!;
    fireEvent.click(within(firstServe as HTMLElement).getByText("Team B"));

    await fillSlot(0, { name: "A1" });
    await fillSlot(0, { name: "A2" });
    await fillSlot(1, { name: "B1" });
    await fillSlot(1, { name: "B2" });
    fireEvent.click(screen.getByText("Start game"));

    await waitFor(() => expect(screen.getByText("Scoring screen")).toBeTruthy());
    expect(bodyOf(calls).first_server).toBe("B");
  });

  it("offers recent guests so the same person can be re-picked", async () => {
    stubFetch();
    wrap();

    fireEvent.click(screen.getAllByText("Add player")[0]);
    await waitFor(() => expect(screen.getByText("Recent guests")).toBeTruthy());
    // Mike is a guest, so he is not in the roster grid but is offered as recent.
    expect(screen.getByText("Mike")).toBeTruthy();
    expect(screen.getByText(/keeps two players called the same name apart/)).toBeTruthy();
  });

  it("a slot can be cleared after picking", async () => {
    stubFetch();
    wrap();

    await fillSlot(0, { player: "Ivo Novak" });
    expect(screen.getByText("Ivo Novak")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Remove Ivo Novak"));
    await waitFor(() => expect(screen.getAllByText("Add player")).toHaveLength(4));
  });

  it("lists finished games with their scores", async () => {
    stubFetch({
      casual: [{
        ...CREATED, match_id: "m9", status: "complete", winner: "A",
        games: [{ a: 11, b: 8 }],
      }],
    });
    wrap();

    await waitFor(() => expect(screen.getByText("11–8 · doubles")).toBeTruthy());
    expect(screen.getByText("Ivo & Mike").className).toContain("pickup-won");
    expect(screen.getByText("Priya & Dave").className).not.toContain("pickup-won");
    // A finished game has nothing to resume.
    expect(screen.queryByText("Resume")).toBeNull();
  });

  it("offers to resume a game still in progress", async () => {
    stubFetch({
      casual: [{ ...CREATED, match_id: "m8", status: "live", games: [{ a: 6, b: 4 }] }],
    });
    wrap();

    await waitFor(() => expect(screen.getByText("Resume")).toBeTruthy());
    fireEvent.click(screen.getByText("Resume"));
    await waitFor(() => expect(screen.getByText("Scoring screen")).toBeTruthy());
  });

  it("shows the empty state when nothing has been played", async () => {
    stubFetch();
    wrap();
    await waitFor(() => expect(screen.getByText("No pickup games yet")).toBeTruthy());
  });
});

describe("searching for a player", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  /** Open the picker for team A's first slot. */
  async function openPicker() {
    const card = document.querySelectorAll(".team-card")[0] as HTMLElement;
    fireEvent.click(within(card).getAllByText("Add player")[0]);
    await waitFor(() => expect(screen.getByText("Ivo Novak")).toBeTruthy());
    return screen.getByPlaceholderText("Search, or type a new name");
  }

  const shownNames = () =>
    [...document.querySelectorAll(".pick-cell span, .guest-chip span")]
      .map((n) => n.textContent);

  it("shows the whole roster before anything is typed", async () => {
    stubFetch();
    wrap();
    await openPicker();
    expect(shownNames()).toEqual([
      "Ivo Novak", "Priya Raman", "Nina Roth", "Nikhil Rao", "Sam Whitfield",
      "Mike", "Mikaela",
    ]);
  });

  it("narrows the list as you type", async () => {
    stubFetch();
    wrap();
    const input = await openPicker();

    fireEvent.change(input, { target: { value: "ni" } });
    await waitFor(() => expect(shownNames()).toEqual(["Nina Roth", "Nikhil Rao"]));

    fireEvent.change(input, { target: { value: "nin" } });
    await waitFor(() => expect(shownNames()).toEqual(["Nina Roth"]));
  });

  it("matches anywhere in the name, not just the start", async () => {
    stubFetch();
    wrap();
    const input = await openPicker();

    fireEvent.change(input, { target: { value: "roth" } });
    await waitFor(() => expect(shownNames()).toEqual(["Nina Roth"]));
  });

  it("ignores case", async () => {
    stubFetch();
    wrap();
    const input = await openPicker();

    fireEvent.change(input, { target: { value: "IVO" } });
    await waitFor(() => expect(shownNames()).toEqual(["Ivo Novak"]));
  });

  it("filters recent guests too", async () => {
    stubFetch();
    wrap();
    const input = await openPicker();

    fireEvent.change(input, { target: { value: "mik" } });
    await waitFor(() => expect(shownNames()).toEqual(["Mike", "Mikaela"]));
  });

  it("offers to add a one-off when nothing matches", async () => {
    stubFetch();
    wrap();
    const input = await openPicker();

    fireEvent.change(input, { target: { value: "Zorro" } });
    await waitFor(() => expect(shownNames()).toEqual([]));
    expect(screen.getByText(/Nobody matches/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(screen.queryByText("Choose a player")).toBeNull());
    // Scoped to the slot: the derived team name reads "Zorro" too.
    expect(
      [...document.querySelectorAll(".slot-name")].map((n) => n.textContent),
    ).toEqual(["Zorro"]);
  });

  it("picking a search result sends its id, not its name", async () => {
    const calls = stubFetch();
    wrap();
    const input = await openPicker();

    fireEvent.change(input, { target: { value: "whit" } });
    await waitFor(() => expect(shownNames()).toEqual(["Sam Whitfield"]));
    fireEvent.click(screen.getByText("Sam Whitfield"));

    await fillSlot(0, { name: "A2" });
    await fillSlot(1, { name: "B1" });
    await fillSlot(1, { name: "B2" });
    fireEvent.click(screen.getByText("Start game"));

    await waitFor(() => expect(screen.getByText("Scoring screen")).toBeTruthy());
    expect(bodyOf(calls).a.players[0]).toEqual({ player_id: "p5" });
  });

  it("the search resets between slots", async () => {
    stubFetch();
    wrap();
    const input = await openPicker();

    fireEvent.change(input, { target: { value: "nin" } });
    await waitFor(() => expect(shownNames()).toEqual(["Nina Roth"]));
    fireEvent.click(screen.getByText("Nina Roth"));
    await waitFor(() => expect(screen.queryByText("Choose a player")).toBeNull());

    // The next slot must not inherit the previous filter.
    const again = await openPicker();
    expect((again as HTMLInputElement).value).toBe("");
    expect(shownNames().length).toBeGreaterThan(1);
  });

  it("someone already picked stays out of the results", async () => {
    stubFetch();
    wrap();
    await fillSlot(0, { player: "Nina Roth" });

    const input = await openPicker();
    fireEvent.change(input, { target: { value: "ni" } });
    // Nina is on team A already; only Nikhil is still available.
    await waitFor(() => expect(shownNames()).toEqual(["Nikhil Rao"]));
  });
});
