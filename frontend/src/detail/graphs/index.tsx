// Renders a GraphSpec (spec §10.7.4) with tiny SVG components styled only by tokens.
// `parseGraph` rejects malformed data, so a bad graph yields `null` and the caller says so.

import type { GraphSpec } from "../../api/types";
import { BarGraph } from "./Bar";
import { parseGraph, type ParsedGraph } from "./data";
import { HeatmapGraph } from "./Heatmap";
import styles from "./graphs.module.css";
import { LineGraph } from "./Line";

const NAME: Record<GraphSpec["template"], string> = {
  bar: "Bar chart",
  line: "Line chart",
  heatmap: "Heatmap",
  before_after: "Before and after bar chart",
  distribution: "Histogram",
};

/** The same numbers as a table, for screen readers (the SVG is a picture of this). */
function DataTable({ parsed, caption }: { parsed: ParsedGraph; caption: string }) {
  if (parsed.kind === "heatmap") {
    const d = parsed.data;
    return (
      <table className={styles.srOnly}>
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col" />
            {d.cols.map((c, j) => (
              <th key={j} scope="col">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {d.rows.map((r, i) => (
            <tr key={i}>
              <th scope="row">{r}</th>
              {(d.values[i] ?? []).map((v, j) => (
                <td key={j}>{v}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  const d = parsed.data;
  return (
    <table className={styles.srOnly}>
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col" />
          {d.series.map((s, j) => (
            <th key={j} scope="col">
              {s.name || "Value"}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {d.x.map((x, i) => (
          <tr key={i}>
            <th scope="row">{x}</th>
            {d.series.map((s, j) => (
              <td key={j}>{s.y[i]}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function canRender(spec: GraphSpec): boolean {
  return parseGraph(spec) !== null;
}

export function GraphView({ spec }: { spec: GraphSpec }) {
  const parsed = parseGraph(spec);
  if (!parsed) return null;
  const label = `${NAME[spec.template]}. ${spec.caption}`;
  return (
    <figure className={styles.figure}>
      <figcaption className={styles.caption}>{spec.caption}</figcaption>
      {parsed.kind === "heatmap" ? (
        <HeatmapGraph data={parsed.data} label={label} />
      ) : parsed.kind === "line" || (parsed.kind === "distribution" && parsed.style === "curve") ? (
        <LineGraph data={parsed.data} label={label} />
      ) : (
        <BarGraph data={parsed.data} colors={"colors" in parsed ? parsed.colors : undefined} label={label} />
      )}
      <DataTable parsed={parsed} caption={spec.caption} />
      {spec.note && <p className={styles.note}>{spec.note}</p>}
    </figure>
  );
}
