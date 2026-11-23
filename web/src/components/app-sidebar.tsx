import {LayoutDashboard, List, FolderGit2, History, Inbox, Settings2} from 'lucide-react';

import {cn} from '@/lib/utils';

export type ConnState = 'connecting' | 'live' | 'reconnecting';

const CONN_STYLE: Record<ConnState, {className: string; dot: string}> = {
  connecting: {className: 'text-foreground/70 bg-secondary', dot: '#b3b5c4'},
  live: {className: 'text-success-foreground bg-success-muted', dot: '#2e9e6b'},
  reconnecting: {className: 'text-warning-foreground bg-warning-muted', dot: '#d98a0b'},
};

export type PageKey =
  | 'dashboard'
  | 'tasks'
  | 'history'
  | 'mapping'
  | 'sources'
  | 'settings'
  | 'report';

export function navKeyOf(page: PageKey, reportFrom?: PageKey): PageKey {
  return page === 'report' && reportFrom && reportFrom !== 'report' ? reportFrom : page;
}

export const NAV_ITEMS: {key: PageKey; label: string; icon: typeof List}[] = [
  {key: 'dashboard', label: '概览', icon: LayoutDashboard},
  {key: 'tasks', label: '任务中心', icon: List},
  {key: 'history', label: '历史记录', icon: History},
  {key: 'mapping', label: '项目映射', icon: FolderGit2},
  {key: 'sources', label: '数据源', icon: Inbox},
  {key: 'settings', label: '系统设置', icon: Settings2},
];

interface AppSidebarProps {
  page: PageKey;
  onPage: (page: PageKey) => void;
  reportFrom?: PageKey;
}

export function AppSidebar({page, onPage, reportFrom}: AppSidebarProps) {
  const activeKey = navKeyOf(page, reportFrom);
  return (
    <aside className="hidden w-[220px] shrink-0 flex-col border-r border-border bg-card lg:flex">
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 py-3">
        {NAV_ITEMS.map((item) => {
          const active = item.key === activeKey;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onPage(item.key)}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13px]',
                active
                  ? 'bg-primary-muted font-semibold text-primary'
                  : 'text-foreground/70 hover:bg-secondary/60 hover:text-foreground',
              )}
            >
              <item.icon className="size-4 shrink-0" />
              <span className="truncate">{item.label}</span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}

export function ConnBadge({conn, connText}: {conn: ConnState; connText: string}) {
  const style = CONN_STYLE[conn];
  return (
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
  );
}
