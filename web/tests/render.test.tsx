// @vitest-environment jsdom
/**
 * Render smoke tests.
 *
 * These catch the class of failure a typecheck cannot: a component that throws
 * on mount, a bad hook order, a route that renders nothing. They stub `fetch`
 * so no server is required.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Auth from "../src/features/Auth";
import Tournaments from "../src/features/Tournaments";
import { AvatarChip, Ball, Empty, Seg } from "../src/components/ui";

function wrap(ui: React.ReactNode, route = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="*" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const jsonResponse = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );

describe("UI components", () => {
  it("renders the ball logo as an svg", () => {
    const { container } = render(<Ball size={24} />);
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("falls back to an initial when there is no avatar", () => {
    render(<AvatarChip player={{ name: "Ann Lee" }} />);
    expect(screen.getByText("A")).toBeTruthy();
  });

  it("renders an emoji avatar", () => {
    render(<AvatarChip player={{ name: "Bo", avatar: { type: "emoji", value: "🏓" } }} />);
    expect(screen.getByText("🏓")).toBeTruthy();
  });

  it("marks the selected segment", () => {
    const { container } = render(
      <Seg
        options={[{ value: "a", label: "Doubles" }, { value: "b", label: "Singles" }]}
        value="a"
        onChange={() => {}}
      />,
    );
    expect(container.querySelector(".seg-on")?.textContent).toBe("Doubles");
  });

  it("renders the empty state", () => {
    render(<Empty icon={null} title="Nothing here" sub="Add something" />);
    expect(screen.getByText("Nothing here")).toBeTruthy();
  });
});

describe("screens", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the sign-in screen", () => {
    wrap(<Auth />);
    expect(screen.getByPlaceholderText("Email")).toBeTruthy();
    expect(screen.getByPlaceholderText("Password")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
  });

  it("lists tournaments returned by the API", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      jsonResponse([
        {
          id: "t1", name: "Spring Open", slug: "spring-open", owner_id: "u1",
          starts_on: "2026-09-12", ends_on: null, timezone: "UTC",
          status: "registration", public_token: "tok",
        },
      ]),
    ));

    wrap(<Tournaments />);
    await waitFor(() => expect(screen.getByText("Spring Open")).toBeTruthy());
    expect(screen.getByText(/registration/)).toBeTruthy();
  });

  it("shows the empty state when there are no tournaments", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse([])));
    wrap(<Tournaments />);
    await waitFor(() => expect(screen.getByText("No tournaments yet")).toBeTruthy());
  });

  it("surfaces an API error rather than rendering blank", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      jsonResponse({ detail: "Something broke" }, 500),
    ));
    wrap(<Tournaments />);
    await waitFor(() => expect(screen.getByText("Something broke")).toBeTruthy());
  });
});
