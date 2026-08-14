// @vitest-environment jsdom
/**
 * The bulk upload screen.
 *
 * The behaviour worth pinning down here is the two-step guard: choosing a file
 * previews it and nothing more, and the Import button stays disabled until the
 * server says the sheet is clean. Everything else on the screen is presentation.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import BulkImport from "../src/features/BulkImport";
import { ApiError, previewFromError } from "../src/lib/api";

function wrap(route = "/import") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/import" element={<BulkImport />} />
          <Route path="/tournaments/:id" element={<div>Tournament page</div>} />
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

const PREVIEW = {
  ok: true,
  tournament_name: "Spring Open",
  tournament_id: null,
  creates_tournament: true,
  divisions: [
    {
      name: "4.0 Mixed",
      format: "mixed",
      draw_kind: "round_robin",
      skill: null,
      age: null,
      best_of: 3,
      pools: 2,
      existing: false,
      entries: [
        {
          row: 2,
          name: "Ivo & Priya",
          seed: 1,
          players: [
            { name: "Ivo Novak", rating: 4.25, existing: true },
            { name: "Priya Raman", rating: null, existing: false },
          ],
        },
      ],
    },
  ],
  problems: [
    { severity: "warning", message: "Seed 1 is used twice", row: 3 },
  ],
  entry_count: 1,
  new_players: 1,
  matched_players: 1,
};

function pickFile(name = "teams.csv") {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["Division,Player 1\n"], name, { type: "text/csv" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  fireEvent.change(input);
  return file;
}

describe("bulk upload", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("offers both templates as plain download links", () => {
    wrap();
    const excel = screen.getByText("Excel").closest("a") as HTMLAnchorElement;
    const csv = screen.getByText("CSV").closest("a") as HTMLAnchorElement;
    // Plain links, not fetches: a link cannot carry the bearer token, which is
    // why these two endpoints are public.
    expect(excel.getAttribute("href")).toBe("/api/v1/imports/template.xlsx");
    expect(csv.getAttribute("href")).toBe("/api/v1/imports/template.csv");
    expect(excel.hasAttribute("download")).toBe(true);
  });

  it("shows nothing to confirm until a file is chosen", () => {
    wrap();
    expect(screen.queryByText(/^Import /)).toBeNull();
  });

  it("previews a chosen file and reports what it found", async () => {
    const fetchMock = vi.fn((_url: string, _init?: RequestInit) =>
      jsonResponse(PREVIEW));
    vi.stubGlobal("fetch", fetchMock);
    wrap();
    pickFile();

    await waitFor(() => expect(screen.getByText("4.0 Mixed")).toBeTruthy());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/imports/preview");
    expect(screen.getByText("Ivo & Priya")).toBeTruthy();
    expect(screen.getByText("mixed · round robin · best of 3")).toBeTruthy();
  });

  it("sends the file as multipart without overriding the boundary", async () => {
    const fetchMock = vi.fn((_url: string, _init?: RequestInit) =>
      jsonResponse(PREVIEW));
    vi.stubGlobal("fetch", fetchMock);
    wrap();
    pickFile();

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const init = fetchMock.mock.calls[0][1]!;
    expect(init.body).toBeInstanceOf(FormData);
    // Setting Content-Type by hand would strip the generated boundary and the
    // server could no longer split the parts.
    expect(new Headers(init.headers).get("Content-Type")).toBeNull();
  });

  it("marks players already on the roster", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(PREVIEW)));
    wrap();
    pickFile();

    await waitFor(() => expect(screen.getByText("Ivo Novak")).toBeTruthy());
    expect(screen.getByText("Ivo Novak").className).toContain("known");
    expect(screen.getByText("Priya Raman").className).not.toContain("known");
  });

  it("surfaces warnings without blocking the import", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(PREVIEW)));
    wrap();
    pickFile();

    await waitFor(() => expect(screen.getByText(/Worth a look/)).toBeTruthy());
    fireEvent.click(screen.getByText(/Worth a look/));
    expect(screen.getByText("Seed 1 is used twice")).toBeTruthy();
  });

  it("will not import until the new tournament has a name", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(PREVIEW)));
    wrap();
    pickFile();

    await waitFor(() => expect(screen.getByText("Import 1 team")).toBeTruthy());
    const button = screen.getByText("Import 1 team").closest("button")!;
    expect(button.disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText("e.g. Spring Open 2026"), {
      target: { value: "Spring Open" },
    });
    expect(button.disabled).toBe(false);
  });

  it("refuses to import a sheet the server rejected", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      jsonResponse({
        ...PREVIEW,
        ok: false,
        problems: [{ severity: "error", message: "Player 2 is blank", row: 4 }],
      }),
    ));
    wrap();
    pickFile();

    await waitFor(() => expect(screen.getByText("Player 2 is blank")).toBeTruthy());
    expect(screen.getByText("Import 1 team").closest("button")!.disabled).toBe(true);
    expect(screen.getByText(/Nothing has been\s+created/)).toBeTruthy();
  });

  it("imports into an existing tournament without asking for a name", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      jsonResponse({ ...PREVIEW, creates_tournament: false, tournament_id: "t1" }),
    ));
    wrap("/import?tournament=t1");
    expect(screen.queryByPlaceholderText("e.g. Spring Open 2026")).toBeNull();
    pickFile();

    await waitFor(() => expect(screen.getByText("Import 1 team")).toBeTruthy());
    expect(screen.getByText("Import 1 team").closest("button")!.disabled).toBe(false);
  });

  it("keeps the row-by-row report when the commit is rejected", async () => {
    const rejection = {
      detail: {
        message: "The sheet has errors that must be fixed first",
        preview: {
          ...PREVIEW,
          ok: false,
          problems: [{ severity: "error", message: "Ivo is entered twice", row: 5 }],
        },
      },
    };
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => jsonResponse(PREVIEW))
      .mockImplementationOnce(() => jsonResponse(rejection, 422));
    vi.stubGlobal("fetch", fetchMock);

    wrap("/import?tournament=t1");
    pickFile();
    await waitFor(() => expect(screen.getByText("Import 1 team")).toBeTruthy());
    fireEvent.click(screen.getByText("Import 1 team"));

    // The whole preview comes back on the rejection, so the organizer keeps the
    // context instead of a bare one-line error.
    await waitFor(() => expect(screen.getByText("Ivo is entered twice")).toBeTruthy());
    expect(screen.getByText("4.0 Mixed")).toBeTruthy();
  });

  it("goes to the new tournament once the import succeeds", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => jsonResponse(PREVIEW))
      .mockImplementationOnce(() =>
        jsonResponse({
          tournament: {
            id: "t9", name: "Spring Open", slug: "spring-open", owner_id: "u1",
            starts_on: null, ends_on: null, timezone: "UTC",
            status: "draft", public_token: "tok",
          },
          tournament_created: true,
          divisions_created: 1,
          divisions_reused: 0,
          entries_created: 1,
          players_created: 1,
          players_matched: 1,
          problems: [],
        }, 201),
      );
    vi.stubGlobal("fetch", fetchMock);

    wrap("/import?tournament=t9");
    pickFile();
    await waitFor(() => expect(screen.getByText("Import 1 team")).toBeTruthy());
    fireEvent.click(screen.getByText("Import 1 team"));

    await waitFor(() => expect(screen.getByText("Tournament page")).toBeTruthy());
  });
});

describe("previewFromError", () => {
  it("unwraps the preview a rejected commit carries back", () => {
    const error = new ApiError("nope", 422, {
      detail: { message: "nope", preview: PREVIEW },
    });
    expect(previewFromError(error)?.entry_count).toBe(1);
  });

  it("returns null for an unrelated failure", () => {
    expect(previewFromError(new ApiError("boom", 500, { detail: "boom" }))).toBeNull();
    expect(previewFromError(new Error("offline"))).toBeNull();
  });
});
