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

export function KindBadge({kind}: {kind: Kind}) {
  return <Badge variant={kindVariant[kind] ?? 'outline'}>{kindLabel[kind] ?? kind}</Badge>;
}

export function Mono({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <code
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