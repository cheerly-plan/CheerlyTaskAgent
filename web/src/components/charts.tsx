import * as React from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

export interface ChartSeries {
  key: string;
  label: string;
  color: string;
  colorFrom?: string;
}

export interface ChartPoint {
  label: string;
  [key: string]: string | number;
}

interface WorkloadChartProps {
  data: ChartPoint[];
  series: ChartSeries[];
  height?: number;
}

function toCount(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.floor(n);
}

function niceStep(max: number): number {
  if (max <= 0) return 1;
  const rough = max / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / magnitude;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return Math.max(1, step * magnitude);
}

function shade(color: string, amount: number): string {
  const hex = color.replace('#', '');
  if (hex.length !== 6) return color;
  const num = Number.parseInt(hex, 16);
  const channel = (shift: number) => (num >> shift) & 255;
  const mix = (c: number) =>
    Math.round(amount >= 0 ? c + (255 - c) * amount : c * (1 + amount));
  return `rgb(${mix(channel(16))}, ${mix(channel(8))}, ${mix(channel(0))})`;
}

interface TipEntry {
  dataKey?: string | number;
  value?: number | string;
  color?: string;
  name?: string | number;
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TipEntry[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((entry) => toCount(entry.value) > 0);
  const total = payload.reduce((sum, entry) => sum + toCount(entry.value), 0);
  return (
    <div className="min-w-[150px] rounded-xl border border-border/70 bg-popover px-3.5 py-3 text-[12px] shadow-[0_12px_32px_-12px_rgba(23,25,40,0.28)]">
      <div className="mb-2 text-[13px] font-semibold text-foreground">{label}</div>
      <div className="mb-2.5 flex items-baseline justify-between gap-4 text-foreground/60">
        <span>总计</span>
        <span className="text-[15px] font-semibold tabular-nums text-foreground">{total}</span>
      </div>
      <div className="space-y-1.5 border-t border-border/60 pt-2">
        {rows.length === 0 ? (
          <div className="text-[11.5px] text-faint">无处理记录</div>
        ) : (
          rows.map((entry) => (
            <div key={String(entry.dataKey)} className="flex items-center gap-2">
              <span
                className="size-2.5 shrink-0 rounded-[3px]"
                style={{background: entry.color}}
              />
              <span className="text-foreground/70">{String(entry.name ?? '')}</span>
              <span className="ml-auto tabular-nums font-medium text-foreground">
                {toCount(entry.value)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export function WorkloadChart({data, series, height = 260}: WorkloadChartProps) {
  const [hoverIndex, setHoverIndex] = React.useState<number | null>(null);

  const safeData = React.useMemo(
    () =>
      data.map((point) => {
        const next: ChartPoint = {label: String(point.label ?? '')};
        for (const s of series) next[s.key] = toCount(point[s.key]);
        let total = 0;
        for (const s of series) total += toCount(point[s.key]);
        next.__total = total;
        return next;
      }),
    [data, series],
  );

  const maxTotal = React.useMemo(
    () => safeData.reduce((max, point) => Math.max(max, toCount(point.__total)), 0),
    [safeData],
  );
  const step = niceStep(maxTotal);
  const yMax = Math.max(step, Math.ceil(maxTotal / step) * step);
  const pointsWithData = safeData.filter((point) => toCount(point.__total) > 0).length;
  const compact = pointsWithData > 20;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={safeData}
        margin={{top: 10, right: 8, bottom: 4, left: -8}}
        barCategoryGap={compact ? '14%' : '30%'}
        style={{outline: 'none'}}
        onMouseMove={(state) => {
          const idx = (state as {activeTooltipIndex?: number}).activeTooltipIndex;
          setHoverIndex(typeof idx === 'number' ? idx : null);
        }}
        onMouseLeave={() => setHoverIndex(null)}
      >
        <defs>
          {series.map((s) => {
            const top = s.colorFrom ?? shade(s.color, 0.3);
            const bottom = s.color;
            return (
              <React.Fragment key={s.key}>
                <linearGradient id={`grad-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={top} />
                  <stop offset="100%" stopColor={bottom} />
                </linearGradient>
              </React.Fragment>
            );
          })}
        </defs>

        <CartesianGrid strokeDasharray="3 5" stroke="hsl(var(--border))" vertical={false} />
        <XAxis
          dataKey="label"
          tick={(props) => {
            const item = props as {x?: string | number; y?: string | number; payload?: {value?: string}; index?: number};
            const active = item.index === hoverIndex;
            return (
              <text
                x={item.x}
                y={Number(item.y ?? 0) + 12}
                textAnchor="middle"
                style={{
                  fontSize: 11,
                  fontWeight: active ? 650 : 400,
                  fill: active ? '#5b54c4' : '#9a9cad',
                }}
              >
                {item.payload?.value}
              </text>
            );
          }}
          tickLine={false}
          axisLine={{stroke: 'hsl(var(--border))'}}
          interval="preserveStartEnd"
          minTickGap={16}
        />
        <YAxis
          tick={{fontSize: 11, fill: '#9a9cad'}}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
          width={40}
          domain={[0, yMax]}
          ticks={Array.from({length: Math.round(yMax / step) + 1}, (_, i) => i * step)}
        />
        <Tooltip
          cursor={false}
          content={<ChartTooltip />}
          wrapperStyle={{outline: 'none'}}
          offset={16}
          allowEscapeViewBox={{x: false, y: true}}
          position={{y: 8}}
        />

        {series.map((s, index) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            name={s.label}
            stackId="workload"
            radius={index === series.length - 1 ? [7, 7, 0, 0] : [0, 0, 0, 0]}
            maxBarSize={compact ? 18 : 44}
            animationDuration={480}
          >
            {safeData.map((point, pointIndex) => {
              const value = toCount(point[s.key]);
              const dimmed = hoverIndex !== null && hoverIndex !== pointIndex;
              return (
                <Cell
                  key={String(point.label)}
                  fill={`url(#grad-${s.key})`}
                  className={hoverIndex === pointIndex ? 'chart-bar-active' : undefined}
                  opacity={dimmed ? 0.45 : 1}
                  style={{outline: 'none'}}
                  stroke="none"
                >
                  {value > 0 ? <title>{`${point.label} · ${s.label} ${value} 个`}</title> : null}
                </Cell>
              );
            })}
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}