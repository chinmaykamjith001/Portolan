// UI state (spec §13). Server data lives in TanStack Query; this is only what the user is doing.

import { create } from "zustand";

export type View = "chart" | "route" | "faq";
export const MASTER = "master";

export interface EdgePopover {
  linkId: string;
  x: number; // screen coords of the edge midpoint
  y: number;
  /** Canvas (flow) coords of the midpoint, so the popover follows pan/zoom. */
  flowX?: number;
  flowY?: number;
}

/** "Show on Chart" / "Find on Route": the view that should scroll to a concept on its next render. */
export interface PendingFocus {
  conceptId: string;
  view: "chart" | "route";
}

export interface UIState {
  paperId: string | null;
  view: View;
  /** Concept ids jumped into, outermost first. [] means the master layer. */
  path: string[];
  selectedId: string | null;
  drawerOpen: boolean;
  edge: EdgePopover | null;
  /** The concept we just came back out of (selected after Back, spec §13.4). */
  cameFrom: string | null;
  findingsOpen: boolean;
  pendingFocus: PendingFocus | null;

  setFindingsOpen: (open: boolean) => void;
  /** Switch to `view` and ask it to reveal `conceptId`; the view clears it when done. */
  focusConcept: (conceptId: string, view: "chart" | "route") => void;
  clearFocus: () => void;
  setPaper: (paperId: string | null) => void;
  setView: (view: View) => void;
  select: (conceptId: string | null, openDrawer?: boolean) => void;
  closeDrawer: () => void;
  jumpIn: (conceptId: string) => void;
  back: () => void;
  jumpTo: (depth: number) => void; // breadcrumb click: keep path[0..depth)
  openEdge: (edge: EdgePopover) => void;
  closeEdge: () => void;
  /** Esc: close popover → close drawer → go back one level. */
  escape: () => void;
}

export const currentLayerId = (s: Pick<UIState, "path">): string =>
  s.path[s.path.length - 1] ?? MASTER;

export const useUI = create<UIState>((set, get) => ({
  paperId: null,
  view: "chart",
  path: [],
  selectedId: null,
  drawerOpen: false,
  edge: null,
  cameFrom: null,
  findingsOpen: false,
  pendingFocus: null,

  setFindingsOpen: (findingsOpen) => set({ findingsOpen }),
  focusConcept: (conceptId, view) => set({ view, pendingFocus: { conceptId, view }, edge: null }),
  clearFocus: () => set({ pendingFocus: null }),
  setPaper: (paperId) =>
    set({
      paperId,
      path: [],
      selectedId: null,
      drawerOpen: false,
      edge: null,
      cameFrom: null,
      findingsOpen: false,
      pendingFocus: null,
    }),
  setView: (view) => set({ view, edge: null }),
  select: (conceptId, openDrawer = true) =>
    set({ selectedId: conceptId, drawerOpen: conceptId !== null && openDrawer, edge: null }),
  closeDrawer: () => set({ drawerOpen: false }),
  jumpIn: (conceptId) =>
    set((s) => ({
      path: [...s.path, conceptId],
      selectedId: null,
      drawerOpen: false,
      edge: null,
      cameFrom: null,
    })),
  back: () =>
    set((s) => {
      const from = s.path[s.path.length - 1];
      if (from === undefined) return s;
      return { path: s.path.slice(0, -1), selectedId: from, cameFrom: from, edge: null };
    }),
  jumpTo: (depth) =>
    set((s) => {
      if (depth >= s.path.length) return s;
      const from = s.path[depth] ?? null;
      return { path: s.path.slice(0, depth), selectedId: from, cameFrom: from, edge: null };
    }),
  openEdge: (edge) => set({ edge }),
  closeEdge: () => set({ edge: null }),
  escape: () => {
    const s = get();
    if (s.edge) set({ edge: null });
    else if (s.drawerOpen) set({ drawerOpen: false });
    else s.back();
  },
}));
