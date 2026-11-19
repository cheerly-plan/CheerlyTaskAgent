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

/**
 * 工单处理量堆叠柱状图（recharts）。
 * 悬停显示当日成功/失败明细，图例可点击隐藏某个系列。
 */
export function WorkloadChart({data, series, height = 260}: WorkloadChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{top: 8, right: 8, bottom: 4, left: -16}} barCategoryGap="28%">
        <CartesianGrid strokeDasharray="3 4" stroke="hsl(var(--border))" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{fontSize: 11, fill: '#9a9cad'}}
          tickLine={false}
          axisLine={{stroke: 'hsl(var(--border))'}}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{fontSize: 11, fill: '#9a9cad'}}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
          width={40}
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
          formatter={(value, name) => [`${value ?? 0} 个`, String(name ?? '')]}
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
            maxBarSize={34}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}