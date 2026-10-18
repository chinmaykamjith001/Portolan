import type { Colors, SeriesData } from "./data";
import { Axes, AxisTitles, fadeOf, fillClass, Legend, M, PLOT, Plot, seriesColor, XLabels, yPos } from "./frame";
import { niceRange } from "./scale";

export function BarGraph({ data, colors, label }: { data: SeriesData; colors?: Colors; label: string }) {
  const all = data.series.flatMap((s) => s.y);
  const range = niceRange(Math.min(...all), Math.max(...all));
  const slot = PLOT.w / data.x.length;
  const k = data.series.length;
  const barW = (slot * 0.74) / k;
  return (
    <>
      <Plot label={label}>
        <Axes range={range} />
        {data.series.map((s, j) =>
          s.y.map((v, i) => {
            const top = Math.min(yPos(range, v), yPos(range, 0));
            return (
              <rect
                key={`${j}-${i}`}
                className={fillClass(seriesColor(colors, j))}
                fillOpacity={fadeOf(colors, j)}
                x={M.l + i * slot + slot * 0.13 + j * barW}
                y={top}
                width={Math.max(1, barW - (slot > 6 ? 1 : 0))}
                height={Math.abs(yPos(range, v) - yPos(range, 0))}
              >
                <title>{`${data.x[i] ?? ""}: ${v}`}</title>
              </rect>
            );
          }),
        )}
        <XLabels x={data.x} />
        <AxisTitles xLabel={data.xLabel} yLabel={data.yLabel} />
      </Plot>
      <Legend names={data.series.map((s) => s.name)} colors={colors} />
    </>
  );
}
