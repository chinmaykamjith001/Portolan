// Global keys (spec §13.10): `?`, `/`, Esc order, and the dialogs they open.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { rankConcepts } from "../src/app/ConceptSearch";
import { App } from "../src/app/App";
import { shortcutFor } from "../src/app/keys";
import type { Settings } from "../src/api/types";
import { useUI } from "../src/state/store";
import { CONCEPT, json, mockFetch, newClient, PAPER, concept } from "./detailHarness";

// The canvas views need layout engines jsdom lacks; these tests are about the shell.
vi.mock("../src/chart/ChartView", () => ({ default: () => <div>chart</div> }));
vi.mock("../src/faq/FaqView", () => ({ default: () => <div>faq</div> }));
vi.mock("../src/route/RouteView", () => ({ default: () => <div>route</div> }));

const SETTINGS: Settings = {
  version: "0.0.1",
  provider: "gemini",
  models: { fast: "gemini/flash-lite", default: "gemini/flash", strong: "gemini/flash" },
  limits: { rpm: 8, rpd: 1400, tpm: 200000 },
  free_tier_notice: true,
  sandbox_mode: "process",
};

const BUDGET = {
  day: "2026-10-18",
  requests_today: 3,
  limits: { rpm: 8, rpd: 1400, tpm: 200000 },
  provider: "gemini",
  models: [],
};

function routes(settings: Settings | null = SETTINGS) {
  mockFetch((url) => {
    if (url.endsWith("/api/health")) return json({ status: "ok" });
    if (url.endsWith("/api/settings")) return settings ? json(settings) : json({ error: { code: "x", message: "x" } }, 404);
    if (url.endsWith("/api/budget")) return json(BUDGET);
    return undefined;
  });
}

function renderApp() {
  return render(<App client={newClient()} />);
}

beforeEach(() => {
  useUI.setState({
    paperId: null,
    view: "chart",
    path: [],
    selectedId: null,
    drawerOpen: false,
    edge: null,
    dialog: null,
    findingsOpen: false,
    pendingFocus: null,
  });
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("shortcutFor", () => {
  const open = { paperId: "p", view: "chart", dialog: null };
  test("? always asks for help; / needs a paper", () => {
    expect(shortcutFor("?", { ...open, paperId: null })).toBe("help");
    expect(shortcutFor("/", { ...open, paperId: null })).toBeNull();
    expect(shortcutFor("/", open)).toBe("search");
  });
  test("/ on the FAQ stays with the ask box", () => {
    expect(shortcutFor("/", { ...open, view: "faq" })).toBeNull();
    expect(shortcutFor("?", { ...open, view: "faq" })).toBe("help");
  });
  test("nothing fires while a dialog is open", () => {
    expect(shortcutFor("?", { ...open, dialog: "help" })).toBeNull();
    expect(shortcutFor("/", { ...open, dialog: "settings" })).toBeNull();
  });
  test("other keys are not ours", () => {
    expect(shortcutFor("f", open)).toBeNull();
  });
});

describe("rankConcepts", () => {
  const all = [
    concept("a", "Scaled Dot-Product Attention", { gist: "Scores keys." }),
    concept("b", "Softmax", { gist: "Turns scores into weights." }),
    concept("c", "Label Smoothing", { gist: "Softens the softmax targets." }),
  ];
  test("title start beats word start beats gist", () => {
    expect(rankConcepts(all, "soft").map((c) => c.id)).toEqual(["b", "c"]);
    expect(rankConcepts(all, "attention").map((c) => c.id)).toEqual(["a"]);
  });
  test("an empty query matches nothing", () => {
    expect(rankConcepts(all, "  ")).toEqual([]);
  });
});

describe("dialogs", () => {
  test("? opens the shortcut list and Esc closes it", async () => {
    routes();
    renderApp();
    fireEvent.keyDown(document.body, { key: "?" });
    const dialog = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveTextContent("Fit the view");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("keys typed into the paper box are not shortcuts", () => {
    routes();
    renderApp();
    fireEvent.keyDown(screen.getByLabelText("Paper"), { key: "?" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("no second dialog opens on top of the first", async () => {
    routes();
    renderApp();
    fireEvent.keyDown(document.body, { key: "?" });
    await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    fireEvent.keyDown(document.body, { key: "?" });
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  test("Tab stays inside the dialog", async () => {
    routes();
    renderApp();
    fireEvent.keyDown(document.body, { key: "?" });
    const dialog = await screen.findByRole("dialog");
    const close = screen.getByRole("button", { name: "Close keyboard shortcuts" });
    close.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    // The only control is Close, so Tab wraps onto itself instead of leaving the dialog.
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  test("Esc order: a dialog closes before anything underneath goes back", () => {
    routes();
    act(() => useUI.setState({ paperId: PAPER, path: ["transformer"], dialog: "help" }));
    useUI.getState().escape();
    expect(useUI.getState().dialog).toBeNull();
    expect(useUI.getState().path).toEqual(["transformer"]);
    useUI.getState().escape();
    expect(useUI.getState().path).toEqual([]);
  });
});

describe("concept search", () => {
  function openWithPaper() {
    routes();
    act(() => useUI.setState({ paperId: PAPER }));
    return renderApp();
  }

  test("/ does nothing before a paper is open", () => {
    routes();
    renderApp();
    fireEvent.keyDown(document.body, { key: "/" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("/ opens the search on the Chart and Route but not the FAQ", async () => {
    openWithPaper();
    fireEvent.keyDown(document.body, { key: "/" });
    expect(await screen.findByRole("dialog", { name: "Search concepts" })).toBeInTheDocument();
    act(() => useUI.getState().closeDialog());
    act(() => useUI.setState({ view: "faq" }));
    fireEvent.keyDown(document.body, { key: "/" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("typing finds a cached concept; Enter jumps to it on the Chart", async () => {
    openWithPaper();
    act(() => useUI.setState({ view: "route" }));
    fireEvent.keyDown(document.body, { key: "/" });
    const box = await screen.findByRole("combobox", { name: "Search concepts" });
    expect(box).toHaveFocus();
    fireEvent.change(box, { target: { value: "self" } });
    expect(await screen.findByRole("option", { name: /Self-attention/ })).toBeInTheDocument();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(useUI.getState().view).toBe("chart");
    expect(useUI.getState().pendingFocus).toEqual({ conceptId: CONCEPT, view: "chart" });
  });

  test("arrow keys move through the matches", async () => {
    openWithPaper();
    fireEvent.keyDown(document.body, { key: "/" });
    const box = await screen.findByRole("combobox", { name: "Search concepts" });
    fireEvent.change(box, { target: { value: "t" } }); // Transformer, Self-attention
    const options = await screen.findAllByRole("option");
    expect(options.length).toBeGreaterThan(1);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
  });

  test("a query with no match says so in words", async () => {
    openWithPaper();
    fireEvent.keyDown(document.body, { key: "/" });
    const box = await screen.findByRole("combobox", { name: "Search concepts" });
    fireEvent.change(box, { target: { value: "zzz" } });
    const dialog = screen.getByRole("dialog", { name: "Search concepts" });
    await waitFor(() => expect(within(dialog).getByRole("status")).toHaveTextContent("No loaded concept matches"));
  });
});

describe("settings", () => {
  test("shows the models, limits and the free-tier notice, and never a key", async () => {
    routes();
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    await waitFor(() => expect(dialog).toHaveTextContent("gemini/flash-lite"));
    expect(dialog).toHaveTextContent("8 per minute");
    expect(dialog).toHaveTextContent("Free Gemini keys may let Google use what you send");
    expect(dialog).toHaveTextContent("the key is never shown here");
    expect(dialog.textContent).not.toMatch(/AIza|AQ\./);
  });

  test("the notice is absent for the mock provider", async () => {
    routes({ ...SETTINGS, provider: "mock", free_tier_notice: false });
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    await waitFor(() => expect(dialog).toHaveTextContent("mock"));
    expect(dialog).not.toHaveTextContent("Free Gemini keys");
  });

  test("an older backend without /api/settings falls back to the budget", async () => {
    routes(null);
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    await waitFor(() => expect(dialog).toHaveTextContent("1,400 per day"));
    expect(dialog).toHaveTextContent("gemini");
    expect(dialog).toHaveTextContent("not reported");
  });

  test("Esc closes it and focus returns to the Settings button", async () => {
    routes();
    renderApp();
    const opener = screen.getByRole("button", { name: "Settings" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });

  test("the theme choice is applied to the page", async () => {
    routes();
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.change(await screen.findByLabelText("Theme"), { target: { value: "dark" } });
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "system" } });
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });
});

