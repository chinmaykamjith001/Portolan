import type { Colors, SeriesData } from "./data";
import {
  Axes,
  AxisTitles,
  fadeOf,
  fillClass,
  Legend,
  M,
  NumericXLabels,
  PLOT,
  Plot,
  seriesColor,
  strokeClass,
  XLabels,
  yPos,
} from "./frame";
import styles from "./graphs.module.css";
import { niceRange } from "./scale";

const DASH = [undefined, styles.dashA, styles.dashB];
const PAD = 8;
const MARKERS_UNTIL = 40; // beyond this many points the line alone is clearer

export function LineGraph({ data, colors, label }: { data: SeriesData; colors?: Colors; label: string }) {
  const all = data.series.flatMap((s) => s.y);
  const range = niceRange(Math.min(...all), Math.max(...all));
  const { xs } = data;
  const lo = xs ? (xs[0] ?? 0) : 0;
  const hi = xs ? (xs[xs.length - 1] ?? 1) : 1;
  const slot = PLOT.w / data.x.length;
  const xAt = (i: number) =>
    xs ? M.l + PAD + (((xs[i] ?? lo) - lo) / (hi - lo || 1)) * (PLOT.w - 2 * PAD) : M.l + slot * (i + 0.5);
  return (
    <>
      <Plot label={label}>
        <Axes range={range} />
        {data.series.map((s, j) => {
          const color = seriesColor(colors, j);
          const pts = s.y.map((v, i) => [xAt(i), yPos(range, v)] as const);
          return (
            <g key={j}>
              <path
                className={`${styles.line} ${strokeClass(color)} ${DASH[j % 3] ?? ""}`}
                strokeOpacity={fadeOf(colors, j)}
                d={pts.map(([px, py], i) => `${i === 0 ? "M" : "L"}${px.toFixed(1)} ${py.toFixed(1)}`).join(" ")}
              />
              {pts.length <= MARKERS_UNTIL &&
                pts.map(([px, py], i) => (
                  <circle key={i} className={fillClass(color)} fillOpacity={fadeOf(colors, j)} cx={px} cy={py} r={2.5}>
                    <title>{`${data.x[i] ?? ""}: ${s.y[i] ?? ""}`}</title>
                  </circle>
                ))}
            </g>
          );
        })}
        {xs ? <NumericXLabels lo={lo} hi={hi} pad={PAD} /> : <XLabels x={data.x} />}
        <AxisTitles xLabel={data.xLabel} yLabel={data.yLabel} />
      </Plot>
      <Legend names={data.series.map((s) => s.name)} colors={colors} />
    </>
  );
}
