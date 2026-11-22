import * as React from 'react';
import {CalendarDays} from 'lucide-react';

import {useApp} from '@/app-context';
import {WorkloadChart, type ChartSeries} from '@/components/charts';
import {KpiCards} from '@/components/kpi-cards';
import {Card} from '@/components/ui/card';
import {SelectField} from '@/components/select-field';
import {ACTIVE_STATUSES} from '@/lib/labels';

const RANGES = [
  {value: '3', label: '最近 3 天'},
  {value: '7', label: '最近 7 天'},
  {value: '30', label: '最近 30 天'},
];

const SERIES: ChartSeries[] = [
  {key: 'succeeded', label: '成功', color: '#5b54c4', colorFrom: '#9d98f0'},
  {key: 'failed', label: '失败', color: '#8b7fe8', colorFrom: '#c4bffa'},
  {key: 'other', label: '其它', color: '#b9b3f0', colorFrom: '#e2dffb'},
];

export function DashboardPage() {
  const {items, mappings} = useApp();

  const counts = React.useMemo(() => {
    const result = {running: 0, succeeded: 0, failed: 0, total: 0};
    for (const item of items) {
      if (item.is_container) continue;
      result.total += 1;
      if (ACTIVE_STATUSES.includes(item.status)) result.running += 1;
      else if (item.status === 'succeeded') result.succeeded += 1;
      else if (item.status === 'failed') result.failed += 1;
    }
    return result;
  }, [items]);

  const [range, setRange] = React.useState('7');

  const daily = React.useMemo(() => {
    const days = Number(range);
    const buckets = new Map<string, {succeeded: number; failed: number; other: number}>();
    const today = new Date();
    const labels: string[] = [];
    for (let i = days - 1; i >= 0; i -= 1) {
      const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
      const key = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      labels.push(key);
      buckets.set(key, {succeeded: 0, failed: 0, other: 0});
    }
    for (const item of items) {
      if (item.is_container) continue;
      if (!ACTIVE_STATUSES.includes(item.status) && item.status !== 'discovered' && item.status !== 'ready') {
      } else {
        continue;
      }
      const stamp = item.created_at ?? '';
      if (!stamp) continue;
      const d = new Date(stamp);
      if (Number.isNaN(d.getTime())) continue;
      const local = new Date(d.getTime() - d.getTimezoneOffset() * 60_000);
      const key = `${String(local.getMonth() + 1).padStart(2, '0')}-${String(local.getDate()).padStart(2, '0')}`;
      const bucket = buckets.get(key);
      if (!bucket) continue;
      if (item.status === 'succeeded') bucket.succeeded += 1;
      else if (item.status === 'failed') bucket.failed += 1;
      else bucket.other += 1;
    }
    return labels.map((label) => ({label, ...buckets.get(label)!}));
  }, [items, range]);

  const dailyTotal = React.useMemo(
    () => daily.reduce((sum, d) => sum + d.succeeded + d.failed + d.other, 0),
    [daily],
  );

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-5">
      <KpiCards
        total={counts.total}
        running={counts.running}
        succeeded={counts.succeeded}
        failed={counts.failed}
        mappings={mappings.length}
      />

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-3.5">
          <div>
            <h3 className="text-[13px] font-semibold text-foreground">工单处理量</h3>
          </div>
          <div className="flex items-center gap-2">
            <SelectField
              size="sm"
              className="w-[122px]"
              value={range}
              options={RANGES}
              onChange={setRange}
            />
          </div>
        </div>
        <div className="px-3 py-4">
          {dailyTotal === 0 ? (
            <div className="flex h-[220px] flex-col items-center justify-center gap-2 text-[12px] text-faint">
              <CalendarDays className="size-5" />
              该区间内还没有结束执行的工单
            </div>
          ) : (
            <WorkloadChart data={daily} series={SERIES} height={260} />
          )}
        </div>
      </Card>
    </div>
  );
}