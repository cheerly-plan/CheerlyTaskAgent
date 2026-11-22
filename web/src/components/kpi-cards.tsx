import {CircleAlert, CircleCheck, FolderGit2, Layers, Loader} from 'lucide-react';

import {cn} from '@/lib/utils';

interface KpiCardsProps {
  total: number;
  running: number;
  succeeded: number;
  failed: number;
  mappings: number;
}

export function KpiCards({total, running, succeeded, failed, mappings}: KpiCardsProps) {
  const cards = [
    {label: '全部工单', value: total, icon: Layers, tone: 'text-primary bg-primary-muted'},
    {
      label: '执行中 / 排队',
      value: running,
      icon: Loader,
      tone: 'text-info-foreground bg-info-muted',
      spin: running > 0,
    },
    {label: '已完成', value: succeeded, icon: CircleCheck, tone: 'text-success-foreground bg-success-muted'},
    {label: '失败待处理', value: failed, icon: CircleAlert, tone: 'text-destructive bg-destructive-muted'},
    {label: '项目映射', value: mappings, icon: FolderGit2, tone: 'text-[#6d3fc0] bg-[#f2edfd]'},
  ];

  return (
    <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      {cards.map((card) => (
        <div
          key={card.label}
          className="flex items-center gap-3 rounded-xl border border-border bg-card px-3.5 py-3 shadow-sm"
        >
          <span className={cn('grid size-9 shrink-0 place-items-center rounded-lg', card.tone)}>
            <card.icon className={cn('size-[17px]', card.spin && 'animate-spin')} />
          </span>
          <div className="min-w-0">
            <div className="text-[19px] font-semibold leading-tight tracking-[-0.02em] text-foreground">
              {card.value}
            </div>
            <div className="truncate text-[11.5px] text-faint">{card.label}</div>
          </div>
        </div>
      ))}
    </div>
  );
}