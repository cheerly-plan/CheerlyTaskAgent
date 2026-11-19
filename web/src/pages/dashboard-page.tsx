import * as React from 'react';
import {ArrowRight} from 'lucide-react';

import {useApp} from '@/app-context';
import {KpiCards} from '@/components/kpi-cards';
import {StatusBadge} from '@/components/status-bits';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {ACTIVE_STATUSES, shortTime} from '@/lib/labels';

/** 数据看板：概览数字 + 最近工单。 */
export function DashboardPage({onOpenTasks}: {onOpenTasks: () => void}) {
  const {items, mappings, itemsTotal} = useApp();

  const counts = React.useMemo(() => {
    const result = {running: 0, succeeded: 0, failed: 0};
    for (const item of items) {
      if (ACTIVE_STATUSES.includes(item.status)) result.running += 1;
      else if (item.status === 'succeeded') result.succeeded += 1;
      else if (item.status === 'failed') result.failed += 1;
    }
    return result;
  }, [items]);

  const recent = React.useMemo(
    () =>
      items
        .slice()
        .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
        .slice(0, 6),
    [items],
  );

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-5">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-foreground">数据看板</h2>
          <p className="mt-1 text-[12px] text-faint">
            共 {itemsTotal ?? items.length} 个工作项 · {mappings.length} 个项目映射
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={onOpenTasks}>
          查看全部任务
          <ArrowRight />
        </Button>
      </div>

      <KpiCards
        total={items.length}
        running={counts.running}
        succeeded={counts.succeeded}
        failed={counts.failed}
        mappings={mappings.length}
      />

      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
          <h3 className="text-[13px] font-semibold text-foreground">最近更新</h3>
          <button
            type="button"
            onClick={onOpenTasks}
            className="text-[11.5px] text-primary hover:underline"
          >
            全部任务
          </button>
        </div>
        {recent.length === 0 ? (
          <p className="px-5 py-10 text-center text-[12px] text-faint">暂无工单</p>
        ) : (
          <ul className="divide-y divide-border">
            {recent.map((item) => (
              <li key={item.id} className="flex items-center gap-3 px-5 py-2.5">
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-foreground">
                  {item.title}
                </span>
                <StatusBadge status={item.status} />
                <span className="w-32 shrink-0 text-right text-[11px] tabular-nums text-faint">
                  {shortTime(item.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}