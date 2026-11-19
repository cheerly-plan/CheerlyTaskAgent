import * as React from 'react';
import {CornerDownRight, GitBranch} from 'lucide-react';

import {useApp} from '@/app-context';
import {KpiCards} from '@/components/kpi-cards';
import {StatusBadge} from '@/components/status-bits';
import {Card} from '@/components/ui/card';
import {ACTIVE_STATUSES, shortTime} from '@/lib/labels';
import {orderForTreeView} from '@/lib/task-tree';
import {cn} from '@/lib/utils';

/** 概览：关键数字 + 最近工单。 */
export function DashboardPage({onOpenTasks}: {onOpenTasks: () => void}) {
  const {items, mappings} = useApp();

  const counts = React.useMemo(() => {
    const result = {running: 0, succeeded: 0, failed: 0, total: 0};
    for (const item of items) {
      if (item.is_container) continue; // 父容器不执行，不计入 KPI
      result.total += 1;
      if (ACTIVE_STATUSES.includes(item.status)) result.running += 1;
      else if (item.status === 'succeeded') result.succeeded += 1;
      else if (item.status === 'failed') result.failed += 1;
    }
    return result;
  }, [items]);

  const recent = React.useMemo(
    () =>
      orderForTreeView(
        items
          .slice()
          .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
          .slice(0, 6),
      ),
    [items],
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
            {recent.map((item) => {
              const isChild = Boolean(item.parent_ref);
              const isContainer = Boolean(item.is_container);
              return (
              <li key={item.id} className="flex items-center gap-3 px-5 py-2.5">
                <span
                  className={cn(
                    'flex min-w-0 flex-1 items-center gap-1.5 truncate text-[12.5px] text-foreground',
                    isChild && 'pl-4',
                  )}
                >
                  {isChild ? (
                    <CornerDownRight className="size-3 shrink-0 text-[#a9a0c4]" aria-label="子需求" />
                  ) : null}
                  {isContainer ? (
                    <GitBranch className="size-3 shrink-0 text-[#6d3fc0]" aria-label="父需求" />
                  ) : null}
                  <span className="truncate">{item.title}</span>
                </span>
                {isContainer ? (
                  <span className="w-[72px] text-right text-xs text-faint">—</span>
                ) : (
                  <StatusBadge status={item.status} />
                )}
                <span className="w-32 shrink-0 text-right text-[11px] tabular-nums text-faint">
                  {shortTime(item.created_at)}
                </span>
              </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}