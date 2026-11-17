import {BrandMark} from '@/components/brand-mark';
import {Button} from '@/components/ui/button';
import {Separator} from '@/components/ui/separator';
import {cn} from '@/lib/utils';
import {Plus} from 'lucide-react';

export type ConnState = 'connecting' | 'live' | 'reconnecting';

const CONN_STYLE: Record<ConnState, {className: string; dot: string}> = {
  connecting: {className: 'text-foreground/70 bg-secondary', dot: '#b3b5c4'},
  live: {className: 'text-success-foreground bg-success-muted', dot: '#2e9e6b'},
  reconnecting: {className: 'text-warning-foreground bg-warning-muted', dot: '#d98a0b'},
};

interface AppHeaderProps {
  conn: ConnState;
  connText: string;
  onNewTask: () => void;
}

export function AppHeader({conn, connText, onNewTask}: AppHeaderProps) {
  const style = CONN_STYLE[conn];
  return (
    <header className="sticky top-0 z-30 border-b border-border/80 bg-white/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-[1520px] items-center justify-between gap-4 px-5 lg:px-8">
        <div className="flex min-w-0 items-center gap-2.5">
          <BrandMark size={28} />
          <div className="flex min-w-0 items-baseline gap-2.5">
            <span className="truncate text-[15px] font-semibold tracking-[-0.02em] text-foreground">
              CheerlyTaskAgent
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[11.5px] font-medium',
              style.className,
            )}
          >
            <span
              className={cn('size-1.5 rounded-full', conn === 'live' && 'animate-pulse')}
              style={{background: style.dot}}
            />
            {connText}
          </span>
          <Separator orientation="vertical" className="mx-1 hidden h-5 sm:block" />
          <Button onClick={onNewTask}>
            <Plus />
            新建任务
          </Button>
        </div>
      </div>
    </header>
  );
}