import {LayoutDashboard, List, FolderGit2, Inbox, Settings2, Plus} from 'lucide-react';

import {BrandMark} from '@/components/brand-mark';
import {Button} from '@/components/ui/button';
import {SelectField} from '@/components/select-field';
import {cn} from '@/lib/utils';

export type ConnState = 'connecting' | 'live' | 'reconnecting';

const CONN_STYLE: Record<ConnState, {className: string; dot: string}> = {
  connecting: {className: 'text-foreground/70 bg-secondary', dot: '#b3b5c4'},
  live: {className: 'text-success-foreground bg-success-muted', dot: '#2e9e6b'},
  reconnecting: {className: 'text-warning-foreground bg-warning-muted', dot: '#d98a0b'},
};

export type PageKey = 'dashboard' | 'tasks' | 'mapping' | 'sources' | 'settings';

export const NAV_ITEMS: {key: PageKey; label: string; icon: typeof List}[] = [
  {key: 'dashboard', label: '数据看板', icon: LayoutDashboard},
  {key: 'tasks', label: '任务列表', icon: List},
  {key: 'mapping', label: '项目映射', icon: FolderGit2},
  {key: 'sources', label: '数据源', icon: Inbox},
  {key: 'settings', label: '系统设置', icon: Settings2},
];

interface AppSidebarProps {
  page: PageKey;
  onPage: (page: PageKey) => void;
}

/** 左侧导航：品牌 + 主操作（新建任务）+ 页面菜单 + 连接状态。 */
export function AppSidebar({page, onPage}: AppSidebarProps) {
  return (
    <aside className="hidden w-[220px] shrink-0 flex-col border-r border-border bg-card lg:flex">
      <div className="flex h-14 items-center gap-2.5 border-b border-border px-4">
        <BrandMark size={26} />
        <span className="truncate text-[14.5px] font-semibold tracking-[-0.02em] text-foreground">
          CheerlyTaskAgent
        </span>
      </div>

      <nav className="flex-1 space-y-0.5 px-2 py-1">
        {NAV_ITEMS.map((item) => {
          const active = item.key === page;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onPage(item.key)}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] transition-colors',
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

/** 执行端连接状态徽标（页面右上角）。 */
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

/** 窄屏顶部栏：品牌 + 页面切换 + 新建任务。 */
export function MobileBar({
  page,
  onPage,
  onNewTask,
}: {
  page: PageKey;
  onPage: (page: PageKey) => void;
  onNewTask: () => void;
}) {
  return (
    <div className="flex items-center gap-2 border-b border-border bg-card px-3 py-2 lg:hidden">
      <BrandMark size={24} />
      <span className="truncate text-[13px] font-semibold text-foreground">CheerlyTaskAgent</span>
      <div className="ml-auto flex items-center gap-2">
        <SelectField
          size="sm"
          className="w-[124px]"
          value={page}
          options={NAV_ITEMS.map((item) => ({value: item.key, label: item.label}))}
          onChange={(value) => onPage(value as PageKey)}
        />
        <Button size="icon-sm" onClick={onNewTask} aria-label="新建任务" title="新建任务">
          <Plus />
        </Button>
      </div>
    </div>
  );
}