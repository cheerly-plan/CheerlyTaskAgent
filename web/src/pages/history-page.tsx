import * as React from 'react';
import {ArrowDown, ArrowUp, History, LayoutGrid, List, RefreshCw, Search, X} from 'lucide-react';

import {useApp} from '@/app-context';
import {EmptyState} from '@/components/status-bits';
import {TaskCard} from '@/components/task-card';
import {
  DEFAULT_SORT,
  TaskTable,
  defaultDirection,
  type SortField,
  type SortState,
} from '@/components/task-table';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {SelectField} from '@/components/select-field';
import {isArchived, kindLabel, sortFields, statusLabel, TERMINAL_STATUSES} from '@/lib/labels';
import {cn} from '@/lib/utils';
import type {WorkItem} from '@/lib/api';

type ViewMode = 'table' | 'card';

interface Filters {
  status: string;
  source: string;
  project: string;
}

const DEFAULT_FILTERS: Filters = {status: '', source: '', project: ''};

function sortValue(item: WorkItem, field: string): string {
  switch (field) {
    case 'created':
      return item.created_at ?? '';
    case 'status':
      return statusLabel[item.status] ?? item.status ?? '';
    case 'source':
      return item.source ?? '';
    case 'repo':
      return item.repo ?? '';
    case 'kind':
      return kindLabel[item.kind] ?? item.kind ?? '';
    default:
      return item.title ?? '';
  }
}

export function HistoryPage({onOpenTasks}: {onOpenTasks?: () => void}) {
  const {
    historyItems,
    historyHasMore,
    historyLoading,
    loadMoreHistory,
    reloadHistory,
    selections,
    setSelection,
    runItemAction,
    mapItemNow,
    mappings,
    models,
    backends,
    ensureBackendProbe,
  } = useApp();

  const [filters, setFilters] = React.useState<Filters>(DEFAULT_FILTERS);
  const [sort, setSort] = React.useState<SortState>(DEFAULT_SORT);
  const [keyword, setKeyword] = React.useState('');
  const [view, setView] = React.useState<ViewMode>('table');
  const [refreshing, setRefreshing] = React.useState(false);
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(20);

  const finished = React.useMemo(() => historyItems.filter((item) => isArchived(item)), [historyItems]);

  const visible = React.useMemo(() => {
    let list = finished.slice();
    if (filters.status) list = list.filter((item) => item.status === filters.status);
    if (filters.source) list = list.filter((item) => item.source === filters.source);
    if (filters.project) {
      list = list.filter((item) =>
        filters.project === '__none__' ? !item.repo : item.repo === filters.project,
      );
    }
    const query = keyword.trim().toLowerCase();
    if (query) {
      list = list.filter((item) =>
        [item.title, item.repo, item.source, item.model, item.stage, item.error, item.id].some((value) =>
          String(value ?? '').toLowerCase().includes(query),
        ),
      );
    }
    list.sort((a, b) => {
      const result = sortValue(a, sort.field).localeCompare(sortValue(b, sort.field), 'zh-CN');
      return sort.direction === 'asc' ? result : -result;
    });
    return list;
  }, [finished, filters, keyword, sort]);

  React.useEffect(() => {
    setPage(1);
  }, [filters, keyword, sort, pageSize]);

  const pageCount = Math.max(1, Math.ceil(visible.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const loadPending = safePage >= pageCount && historyHasMore && !historyLoading;

  React.useEffect(() => {
    if (loadPending) void loadMoreHistory();
  }, [loadPending, loadMoreHistory]);

  const pageItems = React.useMemo(
    () => visible.slice((safePage - 1) * pageSize, safePage * pageSize),
    [visible, safePage, pageSize],
  );

  const toggleSort = (field: SortField) =>
    setSort((prev) =>
      prev.field === field
        ? {field, direction: prev.direction === 'desc' ? 'asc' : 'desc'}
        : {field, direction: defaultDirection(field)},
    );

  const filterActive = Boolean(filters.status || filters.source || filters.project || keyword);
  const resetFilters = () => {
    setFilters(DEFAULT_FILTERS);
    setKeyword('');
  };

  const countText =
    visible.length === finished.length
      ? `${finished.length} 条历史记录`
      : `${visible.length} / ${finished.length} 条历史记录`;

  const refresh = async () => {
    setRefreshing(true);
    try {
      await reloadHistory();
    } finally {
      setRefreshing(false);
    }
  };

  const statusOptions = TERMINAL_STATUSES.map((value) => ({value, label: statusLabel[value] ?? value}));

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Card className="flex max-h-full min-h-0 flex-col overflow-hidden">
        <div className="flex shrink-0 flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div>
            <h3 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground">历史记录</h3>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="icon-sm"
              variant="outline"
              onClick={() => void refresh()}
              aria-label="刷新"
            >
              <RefreshCw className={cn(refreshing && 'animate-spin')} />
            </Button>
          </div>
        </div>

        <div className="flex shrink-0 flex-col gap-3 border-b border-border px-4 py-3 lg:flex-row lg:items-center">
          <div className="relative min-w-0 flex-1 lg:max-w-[260px]">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-faint" />
            <Input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              className="h-8 pl-8 text-xs"
            />
          </div>

          <div className="hidden w-px self-stretch bg-border lg:block" />

          <div className="flex flex-wrap items-center gap-2">
            <SelectField
              size="sm"
              className="w-[118px]"
              value={filters.status}
              onChange={(value) => setFilters((prev) => ({...prev, status: value}))}
              options={[{value: '', label: '全部状态'}, ...statusOptions]}
            />
            <SelectField
              size="sm"
              className="w-[116px]"
              value={filters.source}
              onChange={(value) => setFilters((prev) => ({...prev, source: value}))}
              options={[
                {value: '', label: '全部来源'},
                {value: 'manual', label: '手动'},
                {value: 'tapd', label: 'TAPD'},
              ]}
            />
            <SelectField
              size="sm"
              className="w-[132px]"
              value={filters.project}
              onChange={(value) => setFilters((prev) => ({...prev, project: value}))}
              options={[
                {value: '', label: '全部项目'},
                {value: '__none__', label: '未映射'},
                ...mappings.map((mapping) => ({value: mapping.name, label: mapping.name})),
              ]}
            />
            {view === 'card' ? (
              <div className="flex items-center gap-1.5">
                <span className="text-[11.5px] text-faint">排序</span>
                <SelectField
                  size="sm"
                  className="w-[104px]"
                  value={sort.field}
                  onChange={(value) => setSort({field: value as SortField, direction: sort.direction})}
                  options={sortFields}
                />
                <Button
                  size="icon-sm"
                  variant="outline"
                  onClick={() => setSort((prev) => ({...prev, direction: prev.direction === 'asc' ? 'desc' : 'asc'}))}
                  aria-label="切换升降序"
                >
                  {sort.direction === 'asc' ? <ArrowUp /> : <ArrowDown />}
                </Button>
              </div>
            ) : null}
            {filterActive ? (
              <Button size="xs" variant="ghost" className="text-faint" onClick={resetFilters}>
                <X />
                重置
              </Button>
            ) : null}
          </div>

          <div className="flex items-center gap-2 lg:ml-auto">
            <span className="whitespace-nowrap text-[11.5px] tabular-nums text-faint">{countText}</span>
            <div className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-secondary/80 p-0.5">
              {(
                [
                  {value: 'table', label: '表格', icon: List},
                  {value: 'card', label: '卡片', icon: LayoutGrid},
                ] as const
              ).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setView(option.value)}
                  className={cn(
                    'inline-flex h-7 items-center gap-1.5 rounded-[7px] px-2.5 text-xs font-medium outline-none transition-all focus-visible:ring-2 focus-visible:ring-ring/40',
                    view === option.value
                      ? 'bg-card text-foreground shadow-xs ring-1 ring-black/[.04]'
                      : 'text-foreground/60 hover:text-foreground',
                  )}
                >
                  <option.icon className="size-3.5" />
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {finished.length === 0 ? (
          <EmptyState
            icon={History}
            title="还没有历史记录"
            action={
              onOpenTasks ? (
                <Button variant="outline" size="sm" className="mt-2" onClick={onOpenTasks}>
                  返回任务中心
                </Button>
              ) : undefined
            }
          />
        ) : visible.length === 0 ? (
          <EmptyState
            icon={Search}
            title="没有符合条件的历史记录"
            action={
              <Button variant="outline" size="sm" className="mt-2" onClick={resetFilters}>
                清除筛选
              </Button>
            }
          />
        ) : view === 'table' ? (
          <TaskTable
            items={pageItems}
            containerClassName="min-h-0 flex-1"
            sort={sort}
            onSort={toggleSort}
            selections={selections}
            onSelect={setSelection}
            onAction={(action, id) => void runItemAction(action, id)}
            onMap={(id, repo) => void mapItemNow(id, repo)}
            mappings={mappings}
            models={models}
            backends={backends}
            onProbe={(id) => ensureBackendProbe(id)}
          />
        ) : (
          <div className="grid min-h-0 flex-1 gap-3 overflow-auto bg-[#fafafd] p-4 sm:grid-cols-2 xl:grid-cols-3">
            {pageItems.map((item) => (
              <TaskCard
                key={item.id}
                item={item}
                selection={selections[item.id] ?? {}}
                onSelect={setSelection}
                onAction={(action, id) => void runItemAction(action, id)}
                onMap={(id, repo) => void mapItemNow(id, repo)}
                mappings={mappings}
                models={models}
                backends={backends}
                onProbe={(id) => ensureBackendProbe(id)}
              />
            ))}
          </div>
        )}

        {pageCount > 1 || historyHasMore || historyLoading ? (
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border bg-card px-4 py-2">
            <span className="flex items-center gap-1.5 text-[11.5px] tabular-nums text-faint">
              {historyLoading ? <RefreshCw className="size-3 animate-spin" /> : null}
              共 {finished.length} 条历史记录
              {visible.length !== finished.length || filterActive ? `，筛选出 ${visible.length} 条` : ''}
            </span>
            <div className="flex items-center gap-2">
              <span className="text-[11.5px] text-faint">每页</span>
              <SelectField
                size="sm"
                className="w-[76px]"
                value={String(pageSize)}
                options={[20, 50, 100].map((size) => ({value: String(size), label: `${size} 条`}))}
                onChange={(value) => setPageSize(Number(value) || 20)}
              />
              <Button
                size="xs"
                variant="outline"
                disabled={safePage <= 1}
                onClick={() => setPage(Math.max(1, safePage - 1))}
              >
                上一页
              </Button>
              <span className="tabular-nums text-[11.5px] text-faint">
                {safePage} / {pageCount}
                {historyHasMore ? '+' : ''}
              </span>
              <Button
                size="xs"
                variant="outline"
                disabled={safePage >= pageCount && (historyLoading || !historyHasMore)}
                onClick={() => setPage(Math.min(pageCount, safePage + 1))}
              >
                下一页
              </Button>
            </div>
          </div>
        ) : null}
      </Card>

    </div>
  );
}
