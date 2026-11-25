import {Inbox} from 'lucide-react';
import type * as React from 'react';

import {Badge} from '@/components/ui/badge';
import {cn} from '@/lib/utils';
import type {Kind, Status} from '@/lib/api';
import {kindLabel, kindVariant, statusDot, statusLabel, statusVariant} from '@/lib/labels';

export function StatusBadge({status, className}: {status: Status; className?: string}) {
  return (
    <Badge variant={statusVariant[status] ?? 'outline'} className={cn('gap-1.5', className)}>
      <span
        className={cn('size-1.5 rounded-full', status === 'running' && 'animate-pulse')}
        style={{background: statusDot[status] ?? '#b3b5c4'}}
      />
      {statusLabel[status] ?? status}
    </Badge>
  );
}

export function MergeBadge({state}: {state?: string}) {
  // 成功合并是正常结果，不再额外打「已合并」；只有没合进去的才需要提示。
  // 这个状态已经覆盖了底层执行状态（执行成功但没能写回主仓），调用方
  // 会同时隐藏 StatusBadge，避免出现「失败 + 合并冲突」两个徽章。
  if (state === 'blocked') {
    return (
      <Badge variant="destructive" title="执行已成功，但改动未能合并进主仓库，需人工处理">
        合并冲突
      </Badge>
    );
  }
  return null;
}

export function KindBadge({kind}: {kind: Kind}) {
  return <Badge variant={kindVariant[kind] ?? 'outline'}>{kindLabel[kind] ?? kind}</Badge>;
}

export function Mono({
  children,
  className,
  title,
}: {
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <code
      title={title}
      className={cn(
        'rounded-md bg-secondary/90 px-1.5 py-0.5 font-mono text-[11.5px] text-foreground/80',
        className,
      )}
    >
      {children}
    </code>
  );
}

export function EmptyState({
  icon: Icon = Inbox,
  title,
  action,
  compact,
  className,
}: {
  icon?: React.ComponentType<{className?: string}>;
  title?: string;
  action?: React.ReactNode;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 text-center',
        compact ? 'py-8' : 'py-14',
        className,
      )}
    >
      <div className="grid size-11 place-items-center rounded-full border border-dashed border-[#dcdce8] bg-white/70 text-[#b3b5c4]">
        <Icon className="size-5" />
      </div>
      {title ? <div className="text-sm font-semibold text-foreground/90">{title}</div> : null}
      {action}
    </div>
  );
}