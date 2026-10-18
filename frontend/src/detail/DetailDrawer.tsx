// Detail drawer (spec §13.6): header, Explain · Code · Example · Graph, and actions.
// The title comes from the layer already in the cache, so a click shows something at once (§13.11).

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { keys } from "../api/client";
import { useConcept, useGraph, useLayer, usePaper } from "../api/hooks";
import type { Concept, ConceptPayload, LayerPayload } from "../api/types";
import { GROUNDING_LABEL, GroundingGlyph } from "../design/GroundingGlyph";
import { Button, Chip, Drawer, Shimmer, Tabs, type TabItem } from "../design/primitives";
import { currentLayerId, MASTER, useUI } from "../state/store";
import { anchorLabel } from "./anchors";
import { CodeTab } from "./CodeTab";
import styles from "./detail.module.css";
import { ExampleTab } from "./ExampleTab";
import { ExplainTab } from "./ExplainTab";
import { drawableGraph, GraphTab } from "./GraphTab";

type Tab = "explain" | "code" | "example" | "graph";

/** Code grounding's verdict on the concept; it arrives with node details (M6). */
export type Verdict = "matches" | "mismatch";

/** Concept titles from every layer of this paper already in the query cache. */
function useCachedTitles(paperId: string): (id: string) => string | undefined {
  const qc = useQueryClient();
  return (id) => {
    for (const [, data] of qc.getQueriesData<LayerPayload | null>({ queryKey: ["layer", paperId] })) {
      const c = data?.concepts[id];
      if (c) return c.title;
    }
    return qc.getQueryData<ConceptPayload | null>(keys.concept(paperId, id))?.concept.title;
  };
}

function VerdictLine({ verdict }: { verdict: Verdict | null }) {
  if (verdict === null) return null;
  return verdict === "mismatch" ? (
    <span className={styles.verdictMismatch}>! Mismatch</span>
  ) : (
    <span className={styles.verdict}>Matches paper</span>
  );
}

function AlsoAppearsIn({
  ids,
  exclude,
  title,
}: {
  ids: string[];
  exclude: string[];
  title: (id: string) => string | undefined;
}) {
  const names = ids
    .filter((id) => !exclude.includes(id))
    .map((id) => (id === MASTER ? "the main map" : title(id)))
    .filter((n): n is string => n !== undefined);
  if (names.length === 0) return null;
  return <p className={styles.also}>Also appears in {names.join(", ")}.</p>;
}

export function DetailDrawer() {
  const paperId = useUI((s) => s.paperId);
  const selectedId = useUI((s) => s.selectedId);
  const drawerOpen = useUI((s) => s.drawerOpen);
  const path = useUI((s) => s.path);
  const closeDrawer = useUI((s) => s.closeDrawer);
  const jumpIn = useUI((s) => s.jumpIn);
  const layerId = currentLayerId({ path });

  // Keep showing the last concept while the drawer slides out.
  const [shownId, setShownId] = useState<string | null>(selectedId);
  if (selectedId !== null && selectedId !== shownId) setShownId(selectedId);
  const conceptId = selectedId ?? shownId;
  const open = drawerOpen && selectedId !== null;

  const [tab, setTab] = useState<Tab>("explain");
  const layer = useLayer(paperId, layerId).data ?? null;
  const conceptQ = useConcept(paperId, conceptId);
  const paper = usePaper(paperId).data ?? null;
  const title = useCachedTitles(paperId ?? "");

  // The graph is generated in the background once the explanation is on screen; the tab appears
  // only if there is something to draw. Code and Example are requested only when opened.
  const [explainedFor, setExplainedFor] = useState<string | null>(null);
  const graph = useGraph(paperId, conceptId, explainedFor !== null && explainedFor === conceptId);

  const payload = conceptQ.data ?? null;
  const concept: Concept | undefined =
    payload?.concept ?? (conceptId ? layer?.concepts[conceptId] : undefined);
  const center = layer ? layer.concepts[layer.layer.center] : undefined;
  const hasRepo = paper ? Boolean(paper.repo) : null;
  const tabs: TabItem<Tab>[] = [
    { id: "explain", label: "Explain" },
    { id: "code", label: "Code" },
    { id: "example", label: "Example" },
    ...(payload?.details.graph || drawableGraph(graph.data) ? [{ id: "graph" as const, label: "Graph" }] : []),
  ];
  const activeTab: Tab = tabs.some((t) => t.id === tab) ? tab : "explain";
  const canJump = Boolean(concept?.has_layer && !concept.is_first_principle && concept.id !== layer?.layer.center);

  // Move focus to the title when the drawer opens; while closed, nothing inside is focusable.
  const titleRef = useRef<HTMLHeadingElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open && !wasOpen.current) titleRef.current?.focus({ preventScroll: true });
    wasOpen.current = open;
    if (open) innerRef.current?.removeAttribute("inert");
    else innerRef.current?.setAttribute("inert", "");
  }, [open]);

  return (
    <Drawer open={open} wide={activeTab === "code"} label="Concept details">
      <div ref={innerRef} className={styles.root}>
        {paperId && conceptId && (
          <>
            <header className={styles.header}>
              <div className={styles.topRow}>
                <p className={styles.context}>
                  {center ? (
                    <>
                      On the <em>{center.title}</em> map
                    </>
                  ) : (
                    <Shimmer width="40%" />
                  )}
                </p>
                <Button quiet aria-label="Close" onClick={closeDrawer}>
                  Close
                </Button>
              </div>
              <h2 ref={titleRef} tabIndex={-1} className={styles.title}>
                {concept ? concept.title : <Shimmer width="60%" />}
              </h2>
              <div className={styles.facts}>
                {concept && (
                  <span className={styles.grounding}>
                    <GroundingGlyph grounding={concept.grounding} />
                    {GROUNDING_LABEL[concept.grounding]}
                  </span>
                )}
                <VerdictLine verdict={null} />
              </div>
              {concept && concept.anchors.length > 0 && (
                <div className={styles.chips} aria-label="In the paper">
                  {concept.anchors.map((id) => (
                    <Chip key={id} title={id}>
                      {anchorLabel(id)}
                    </Chip>
                  ))}
                </div>
              )}
              {payload && (
                <AlsoAppearsIn ids={payload.also_appears_in} exclude={[layerId, conceptId]} title={title} />
              )}
            </header>
            <div className={styles.tabs}>
              <Tabs<Tab> label="Details" items={tabs} value={activeTab} onChange={setTab} />
            </div>
            <div className={styles.body} role="tabpanel" aria-label={tabs.find((t) => t.id === activeTab)?.label}>
              {activeTab === "explain" && (
                <ExplainTab
                  key={conceptId}
                  paperId={paperId}
                  conceptId={conceptId}
                  onReady={() => setExplainedFor(conceptId)}
                />
              )}
              {activeTab === "code" && (
                <CodeTab
                  key={conceptId}
                  paperId={paperId}
                  conceptId={conceptId}
                  hasRepo={hasRepo}
                  onShowExample={() => setTab("example")}
                />
              )}
              {activeTab === "example" && <ExampleTab key={conceptId} paperId={paperId} conceptId={conceptId} />}
              {activeTab === "graph" && <GraphTab key={conceptId} paperId={paperId} conceptId={conceptId} />}
            </div>
            {canJump && concept && (
              <footer className={styles.actions}>
                <Button onClick={() => jumpIn(concept.id)}>Jump in</Button>
              </footer>
            )}
          </>
        )}
      </div>
    </Drawer>
  );
}
