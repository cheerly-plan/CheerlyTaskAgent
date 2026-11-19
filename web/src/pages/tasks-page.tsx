import * as React from 'react';
import {ArrowDown, ArrowUp, LayoutGrid, List, Plus, RefreshCw, Search, Wand2, X} from 'lucide-react';

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
import {cn} from '@/lib/utils';
import {isArchived, kindLabel, sortFields, statusLabel} from '@/lib/labels';
import type {WorkItem} from '@/lib/api';

type ViewMode = 'table' | 'card';

interface Filters {
  source: string;
  status: string;
  project: string;
}

const DEFAULT_FILTERS: Filters = {source: '', status: '', project: ''};

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

export function TasksPage({onOpenHistory}: {onOpenHistory?: () => void}) {
  const {
    items,
    itemsTotal,
    settings,
    openTapdSync,
    mappings,
    models,
    backends,
    selections,
    setSelection,
    runItemAction,
    mapItemNow,
    openNewTask,
    reloadItems,
    autoMapItems,
    ensureBackendProbe,
  } = useApp();

  const [filters, setFilters] = React.useState<Filters>(DEFAULT_FILTERS);
  const [sort, setSort] = React.useState<SortState>(DEFAULT_SORT);
  const [keyword, setKeyword] = React.useState('');
  const [view, setView] = React.useState<ViewMode>('table');
  const [refreshing, setRefreshing] = React.useState(false);
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(20);

  const tapd = settings?.data_source?.tapd ?? settings?.tapd;
  const tapdReady = Boolean(tapd?.workspace_id && tapd?.api_user && tapd?.api_password);

  // 只有 apply 的终态会归档到「执行历史」；dry-run 结果留在列表，方便接着 apply。
  const activeItems = React.useMemo(
    () => items.filter((item) => !isArchived(item)),
    [items],
  );

  const visible = React.useMemo(() => {
    let list = activeItems.slice();
    if (filters.source) list = list.filter((item) => item.source === filters.source);
    if (filters.status) list = list.filter((item) => item.status === filters.status);
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
  }, [activeItems, filters, keyword, sort]);

  // 筛选、搜索、排序或每页数量变化后回到第一页，避免停在越界页。
  React.useEffect(() => {
    setPage(1);
  }, [filters, keyword, sort, pageSize]);

  const pageCount = Math.max(1, Math.ceil(visible.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const pageItems = React.useMemo(
    () => visible.slice((safePage - 1) * pageSize, safePage * pageSize),
    [visible, safePage, pageSize],
  );

  const toggleSort = (field: SortField) =>
    setSort((prev) =>
      prev.field === field
        ? {field, direction: prev.direction === 'asc' ? 'desc' : 'asc'}
        : {field, direction: defaultDirection(field)},
    );

  const filterActive = Boolean(
    filters.source || filters.status || filters.project || keyword,
  );
  const resetFilters = () => {
    setFilters(DEFAULT_FILTERS);
    setKeyword('');
  };

  const countText =
    visible.length === activeItems.length
      ? `${activeItems.length} 个工作项`
      : `${visible.length} / ${activeItems.length} 个工作项`;

  const refresh = async () => {
    setRefreshing(true);
    try {
      await reloadItems();
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Card className="flex max-h-full min-h-0 flex-col overflow-hidden">
        <div className="flex shrink-0 flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div>
            <h3 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground">任务列表</h3>
          </div>
          <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => openTapdSync(false)}
            disabled={!tapdReady}
            title={tapdReady ? '增量预览：列出上次同步后修改的条目，确认后再导入' : '请先在「数据源」配置 TAPD 与 API 凭证'}
          >
            <RefreshCw />
            立即同步
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => openTapdSync(true)}
            disabled={!tapdReady}
            title={tapdReady ? '全量预览：忽略增量游标，列出全部工单，确认后再导入' : '请先在「数据源」配置 TAPD 与 API 凭证'}
          >
            <RefreshCw />
            全量重拉
          </Button>
          <Button size="sm" onClick={openNewTask}>
            <Plus />
            新建任务
          </Button>
          </div>
        </div>

        {/* 工具栏：搜索 / 筛选 / 视图切换 */}
        <div className="flex shrink-0 flex-col gap-3 border-b border-border px-4 py-3 lg:flex-row lg:items-center">
          <div className="relative min-w-0 flex-1 lg:max-w-[260px]">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-faint" />
            <Input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="请输入关键词搜索"
              className="h-8 pl-8 text-xs"
            />
          </div>

          <div className="hidden w-px self-stretch bg-border lg:block" />

          <div className="flex flex-wrap items-center gap-2">
            <SelectField
              size="sm"
              className="w-[116px]"
              value={filters.source}
              title="来源"
              onChange={(value) => setFilters((prev) => ({...prev, source: value}))}
              options={[
                {value: '', label: '全部来源'},
                {value: 'manual', label: '手动'},
                {value: 'tapd', label: 'TAPD'},
              ]}
            />
            <SelectField
              size="sm"
              className="w-[118px]"
              value={filters.status}
              title="状态"
              onChange={(value) => setFilters((prev) => ({...prev, status: value}))}
              options={[
                {value: '', label: '全部状态'},
                ...Object.entries(statusLabel).map(([value, label]) => ({value, label})),
              ]}
            />
            <SelectField
              size="sm"
              className="w-[132px]"
              value={filters.project}
              title="项目"
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
                  title="排序字段"
                  onChange={(value) => setSort({field: value as SortField, direction: sort.direction})}
                  options={sortFields}
                />
                <Button
                  size="icon-sm"
                  variant="outline"
                  onClick={() => setSort((prev) => ({...prev, direction: prev.direction === 'asc' ? 'desc' : 'asc'}))}
                  title={sort.direction === 'asc' ? '当前升序，点击改为降序' : '当前降序，点击改为升序'}
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
            <Button
              size="icon-sm"
              variant="outline"
              onClick={() => void autoMapItems()}
              title="按 TAPD 项目绑定，自动映射未分配仓库的工单"
              aria-label="自动映射"
            >
              <Wand2 />
            </Button>
            <Button size="icon-sm" variant="outline" onClick={() => void refresh()} title="刷新">
              <RefreshCw className={cn(refreshing && 'animate-spin')} />
            </Button>
          </div>
        </div>

        {activeItems.length === 0 ? (
          <EmptyState
            title={items.length === 0 ? '还没有任何工作项' : '当前没有待处理的任务'}
            description={
              items.length === 0
                ? '点右上角「新建任务」手动录入需求或 Bug；TAPD 工单在「数据源」Tab 自动接入。'
                : '所有任务都已执行结束并归档。可在「执行历史」里查看报告或重新执行。'
            }
            action={
              items.length === 0 ? (
                <Button className="mt-2" onClick={() => openNewTask()}>
                  新建任务
                </Button>
              ) : onOpenHistory ? (
                <Button variant="outline" className="mt-2" onClick={onOpenHistory}>
                  查看执行历史
                </Button>
              ) : undefined
            }
          />
        ) : visible.length === 0 ? (
          <EmptyState
            icon={Search}
            title="没有符合条件的工作项"
            description="试试调整筛选条件或搜索关键词。"
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

        {pageCount > 1 ? (
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border bg-card px-4 py-2">
            <span className="text-[11.5px] tabular-nums text-faint">
              共 {activeItems.length} 条待处理任务
              {visible.length !== activeItems.length || filterActive ? `，筛选出 ${visible.length} 条` : ''}
              {itemsTotal > items.length ? `（当前已加载最近 ${items.length} 条）` : ''}
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
              </span>
              <Button
                size="xs"
                variant="outline"
                disabled={safePage >= pageCount}
                onClick={() => setPage(Math.min(pageCount, safePage + 1))}
              >
                下一页
              </Button>
            </div>
          </div>
        ) : null}
      </Card>

      <p className="mt-3 shrink-0 text-[11.5px] text-faint">
        提示：点「报告」查看执行详情；执行结束的任务会自动归档到「执行历史」。apply 会在隔离 worktree 中真实改代码，执行前需二次确认。
      </p>
    </div>
  );
}