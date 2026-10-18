import type { HeatmapData } from "./data";
import { AxisTitles, H, Plot, W } from "./frame";
import styles from "./graphs.module.css";
import { fit, fmt } from "./scale";

const STEPS = [0.12, 0.3, 0.5, 0.72, 0.95]; // opacity steps of --accent
const L = 64;
const T = 22;
const B = 46;

export function HeatmapGraph({ data, label }: { data: HeatmapData; label: string }) {
  const flat = data.values.flat();
  const lo = Math.min(...flat);
  const hi = Math.max(...flat);
  const cw = (W - L - 8) / data.cols.length;
  const ch = Math.min(28, (H - T - B) / data.rows.length);
  const step = (v: number) => (hi === lo ? 2 : Math.min(STEPS.length - 1, Math.floor(((v - lo) / (hi - lo)) * STEPS.length)));
  return (
    <Plot label={label}>
      {data.rows.map((r, i) => (
        <text key={`r${i}`} className={styles.tick} x={L - 6} y={T + ch * (i + 0.5) + 4} textAnchor="end">
          <title>{r}</title>
          {fit(r, L - 8)}
        </text>
      ))}
      {data.cols.map((c, j) => (
        <text key={`c${j}`} className={styles.tick} x={L + cw * (j + 0.5)} y={T + ch * data.rows.length + 16} textAnchor="middle">
          <title>{c}</title>
          {fit(c, cw)}
        </text>
      ))}
      {data.values.map((row, i) =>
        row.map((v, j) => {
          const s = step(v);
          return (
            <g key={`${i}-${j}`}>
              <rect
                className={styles.fillAccent}
                fillOpacity={STEPS[s]}
                x={L + cw * j + 0.5}
                y={T + ch * i + 0.5}
                width={cw - 1}
                height={ch - 1}
              >
                <title>{`${data.rows[i] ?? ""} × ${data.cols[j] ?? ""}: ${v}`}</title>
              </rect>
              {cw >= 30 && ch >= 16 && (
                <text
                  className={`${styles.cellText} ${s >= 3 ? styles.cellOn : styles.cellOff}`}
                  x={L + cw * (j + 0.5)}
                  y={T + ch * (i + 0.5) + 4}
                  textAnchor="middle"
                >
                  {fmt(v)}
                </text>
              )}
            </g>
          );
        }),
      )}
      <AxisTitles xLabel={data.xLabel} yLabel={data.yLabel} left={8} />
    </Plot>
  );
}
