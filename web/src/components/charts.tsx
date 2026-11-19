import * as React from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

export interface ChartSeries {
  key: string;
  label: string;
  color: string;
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

/**
 * 根据数据量级选一个整齐的刻度步长（1/2/5 × 10^n）。
 * 工单量是计数量级（几十到几千），不跟着异常值放大到百万级别。
 */
function niceStep(max: number): number {
  if (max <= 0) return 1;
  const targetTicks = 4;
  const rough = max / targetTicks;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / magnitude;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return Math.max(1, step * magnitude);
}

/**
 * 工单处理量堆叠柱状图（recharts）。
 * - 所有值先收敛为整数计数，异常值不会把 Y 轴撑到天文数字
 * - Y 轴刻度用整齐步长，写「个」而不是原始大数
 * - 悬停显示当日成功/失败明细，图例可点击隐藏某个系列
 */
export function WorkloadChart({data, series, height = 260}: WorkloadChartProps) {
  // 兜底清洗：即使上游数据异常，图也不会崩坏。
  const safeData = React.useMemo(
    () =>
      data.map((point) => {
        const next: ChartPoint = {label: String(point.label ?? '')};
        for (const s of series) next[s.key] = toCount(point[s.key]);
        return next;
      }),
    [data, series],
  );

  const maxTotal = React.useMemo(() => {
    let max = 0;
    for (const point of safeData) {
      const total = series.reduce((sum, s) => sum + toCount(point[s.key]), 0);
      if (total > max) max = total;
    }
    return max;
  }, [safeData, series]);

  const step = niceStep(maxTotal);
  const yMax = Math.max(step, Math.ceil(maxTotal / step) * step);

  const pointsWithData = safeData.filter((point) =>
    series.some((s) => toCount(point[s.key]) > 0),
  ).length;

  // 单日只有一类数据时用同一个颜色画整根柱子，避免「颜色突变」的误解。
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={safeData}
        margin={{top: 8, right: 8, bottom: 4, left: -8}}
        barCategoryGap={pointsWithData > 20 ? '18%' : '34%'}
      >
        <CartesianGrid strokeDasharray="3 4" stroke="hsl(var(--border))" vertical={false} />
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
          width={44}
          domain={[0, yMax]}
          ticks={Array.from({length: Math.round(yMax / step) + 1}, (_, i) => i * step)}
          tickFormatter={(value) => `${value}`}
        />
        <Tooltip
          cursor={{fill: 'rgba(91,84,196,0.06)'}}
          contentStyle={{
            borderRadius: 10,
            border: '1px solid hsl(var(--border))',
            fontSize: 12,
            boxShadow: '0 8px 24px -12px rgba(23,25,40,0.28)',
            padding: '8px 10px',
          }}
          labelStyle={{fontWeight: 600, marginBottom: 4, color: 'hsl(var(--foreground))'}}
          formatter={(value, name) => [`${toCount(value)} 个`, String(name ?? '')]}
        />
        <Legend
          verticalAlign="top"
          align="right"
          height={28}
          iconType="circle"
          iconSize={8}
          wrapperStyle={{fontSize: 11.5, color: '#6b6e80', paddingBottom: 6}}
        />
        {series.map((s, index) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            name={s.label}
            stackId="workload"
            fill={s.color}
            radius={index === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
            maxBarSize={pointsWithData > 20 ? 14 : 36}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}