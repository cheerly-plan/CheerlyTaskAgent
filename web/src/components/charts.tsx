import * as React from 'react';

export interface ChartSeries {
  key: string;
  label: string;
  /** 线/柱颜色，直接用 hex，避免依赖 tailwind 动态类名。 */
  color: string;
}

export interface ChartPoint {
  /** X 轴标签（如 11-19）。 */
  label: string;
  [key: string]: string | number;
}

interface StackedBarChartProps {
  data: ChartPoint[];
  series: ChartSeries[];
  height?: number;
  /** Y 轴刻度数量。 */
  ticks?: number;
}

const PADDING = {top: 12, right: 8, bottom: 26, left: 30};

/**
 * 轻量堆叠柱状图（纯 SVG，无第三方图表库）。
 * 用于概览页展示每天处理掉的工单数量：成功/失败/其它分色堆叠。
 */
export function StackedBarChart({data, series, height = 220, ticks = 4}: StackedBarChartProps) {
  const [hover, setHover] = React.useState<number | null>(null);
  const width = 640; // viewBox 宽度，实际渲染按容器缩放
  const innerW = width - PADDING.left - PADDING.right;
  const innerH = height - PADDING.top - PADDING.bottom;

  const totals = data.map((point) =>
    series.reduce((sum, s) => sum + Number(point[s.key] ?? 0), 0),
  );
  const max = Math.max(1, ...totals);
  // 取一个好看的整数上限（4 等分刻度）
  const step = Math.max(1, Math.ceil(max / ticks));
  const yMax = step * ticks;

  const slot = innerW / Math.max(1, data.length);
  const barW = Math.max(4, Math.min(28, slot * 0.55));

  const y = (value: number) => PADDING.top + innerH - (value / yMax) * innerH;

  return (
    <div className="relative w-full">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label="每日工单处理量"
        onMouseLeave={() => setHover(null)}
      >
        {/* 网格线 + Y 轴刻度 */}
        {Array.from({length: ticks + 1}, (_, i) => {
          const value = step * i;
          const gy = y(value);
          return (
            <g key={value}>
              <line
                x1={PADDING.left}
                x2={width - PADDING.right}
                y1={gy}
                y2={gy}
                stroke="hsl(var(--border))"
                strokeWidth={1}
                strokeDasharray={i === 0 ? undefined : '3 4'}
              />
              <text
                x={PADDING.left - 6}
                y={gy + 3.5}
                textAnchor="end"
                className="fill-[hsl(var(--faint,240_8%_62%))] text-[10px]"
                style={{fill: '#9a9cad', fontSize: 10}}
              >
                {value}
              </text>
            </g>
          );
        })}

        {/* 柱体 */}
        {data.map((point, index) => {
          const cx = PADDING.left + slot * index + slot / 2;
          let acc = 0;
          return (
            <g
              key={point.label}
              onMouseEnter={() => setHover(index)}
              style={{cursor: 'default'}}
            >
              {/* 命中区域，比柱子宽，方便 hover */}
              <rect
                x={cx - slot / 2}
                y={PADDING.top}
                width={slot}
                height={innerH}
                fill={hover === index ? 'rgba(91,84,196,0.06)' : 'transparent'}
              />
              {series.map((s) => {
                const value = Number(point[s.key] ?? 0);
                if (value <= 0) return null;
                const barH = (value / yMax) * innerH;
                const top = PADDING.top + innerH - acc - barH;
                acc += barH;
                return (
                  <rect
                    key={s.key}
                    x={cx - barW / 2}
                    y={top}
                    width={barW}
                    height={barH}
                    fill={s.color}
                    rx={2}
                  />
                );
              })}
              <text
                x={cx}
                y={height - 8}
                textAnchor="middle"
                style={{fill: '#9a9cad', fontSize: 10}}
              >
                {point.label}
              </text>
            </g>
          );
        })}
      </svg>

      {/* hover 明细 */}
      {hover !== null && data[hover] ? (
        <div className="pointer-events-none absolute left-1/2 top-2 -translate-x-1/2 rounded-lg border border-border bg-popover px-2.5 py-1.5 text-[11.5px] shadow-md">
          <div className="mb-1 font-medium text-foreground">{data[hover].label}</div>
          {series.map((s) => (
            <div key={s.key} className="flex items-center gap-1.5 text-faint">
              <span className="size-2 rounded-sm" style={{background: s.color}} />
              {s.label}
              <span className="ml-auto pl-3 tabular-nums text-foreground">
                {Number(data[hover]![s.key] ?? 0)}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** 图例。 */
export function ChartLegend({series}: {series: ChartSeries[]}) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-[11.5px] text-faint">
      {series.map((s) => (
        <span key={s.key} className="inline-flex items-center gap-1.5">
          <span className="size-2 rounded-sm" style={{background: s.color}} />
          {s.label}
        </span>
      ))}
    </div>
  );
}