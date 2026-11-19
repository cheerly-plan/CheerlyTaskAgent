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
  /** 主色（柱体顶部）。 */
  color: string;
  /** 渐变的深色端，缺省由主色推导。 */
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

/** 把任意输入收敛成「非负有限整数」，挡住 null / NaN / Infinity / 字符串 / 负数。 */
function toCount(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.floor(n);
}

/** 根据数据量级选一个整齐的刻度步长（1/2/5 × 10^n）。 */
function niceStep(max: number): number {
  if (max <= 0) return 1;
  const rough = max / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / magnitude;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return Math.max(1, step * magnitude);
}

/** 主色 → 更亮/更深的 HSL，用于渐变两端。 */
function shade(color: string, amount: number): string {
  const hex = color.replace('#', '');
  if (hex.length !== 6) return color;
  const num = Number.parseInt(hex, 16);
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;
  const mix = (channel: number) =>
    Math.round(amount >= 0 ? channel + (255 - channel) * amount : channel * (1 + amount));
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`;
}

interface TipEntry {
  dataKey?: string | number;
  value?: number | string;
  color?: string;
  name?: string | number;
}

/** 自定义 tooltip：日期标题 + 分类明细 + 总数，带色点和小细线。 */
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
    <div className="min-w-[132px] rounded-xl border border-border bg-popover/95 px-3 py-2 text-[11.5px] shadow-lg backdrop-blur">
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <span className="font-semibold text-foreground">{label}</span>
        <span className="tabular-nums text-faint">共 {total} 个</span>
      </div>
      <div className="space-y-1">
        {rows.length === 0 ? (
          <div className="text-faint">无处理记录</div>
        ) : (
          rows.map((entry) => (
            <div key={String(entry.dataKey)} className="flex items-center gap-2">
              <span
                className="size-2 shrink-0 rounded-full"
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

/**
 * 工单处理量堆叠柱状图（recharts）。
 * - 渐变填充 + 圆角，悬停时该列整体提亮
 * - 最大那天标注总数，其余隔几根标一次，避免数字铺满
 * - 自定义 tooltip（日期 / 分类 / 合计）
 * - 兜底：异常值收敛为 0，Y 轴用整齐步长，不会被撑爆
 */
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
        next.__top = total > 0 ? 0.001 : 0;
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
        barCategoryGap={compact ? '12%' : '22%'}
        onMouseMove={(state) => {
          const idx = (state as {activeTooltipIndex?: number}).activeTooltipIndex;
          setHoverIndex(typeof idx === 'number' ? idx : null);
        }}
        onMouseLeave={() => setHoverIndex(null)}
      >
        <defs>
          {series.map((s) => (
            <React.Fragment key={s.key}>
              <linearGradient id={`grad-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={shade(s.color, 0.22)} stopOpacity={1} />
                <stop offset="100%" stopColor={s.colorFrom ?? shade(s.color, -0.12)} stopOpacity={1} />
              </linearGradient>
              <linearGradient id={`grad-${s.key}-hover`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={shade(s.color, 0.42)} stopOpacity={1} />
                <stop offset="100%" stopColor={s.colorFrom ?? shade(s.color, 0.06)} stopOpacity={1} />
              </linearGradient>
            </React.Fragment>
          ))}
        </defs>

        <CartesianGrid strokeDasharray="3 5" stroke="hsl(var(--border))" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{fontSize: 11, fill: '#9a9cad'}}
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
          cursor={{fill: 'rgba(91,84,196,0.05)', radius: 8}}
          content={<ChartTooltip />}
        />

        {series.map((s, index) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            name={s.label}
            stackId="workload"
            fill={`url(#grad-${s.key})`}
            radius={index === series.length - 1 ? [6, 6, 0, 0] : [0, 0, 0, 0]}
            maxBarSize={compact ? 18 : 52}
            animationDuration={520}
          >
            {safeData.map((point, pointIndex) => {
              const value = toCount(point[s.key]);
              return (
                <Cell
                  key={String(point.label)}
                  fill={`url(#grad-${s.key}${hoverIndex === pointIndex ? '-hover' : ''})`}
                  // 段悬停也能看到该段数量
                  className="transition-opacity"
                  opacity={hoverIndex === null || hoverIndex === pointIndex ? 1 : 0.55}
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