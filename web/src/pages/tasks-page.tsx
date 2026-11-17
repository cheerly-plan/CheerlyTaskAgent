import * as React from 'react';
import {ArrowDown, ArrowUp, LayoutGrid, List, RefreshCw, Search, X} from 'lucide-react';

import {useApp} from '@/app-context';
import {KpiCards} from '@/components/kpi-cards';
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
import {ACTIVE_STATUSES, kindLabel, sortFields, statusLabel} from '@/lib/labels';
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

export function TasksPage() {
  const {
    items,
    mappings,
    models,
    backends,
    selections,
    setSelection,
    runItemAction,
    openReport,
    openNewTask,
    reloadItems,
  } = useApp();

  const [filters, setFilters] = React.useState<Filters>(DEFAULT_FILTERS);
  const [sort, setSort] = React.useState<SortState>(DEFAULT_SORT);
  const [keyword, setKeyword] = React.useState('');
  const [view, setView] = React.useState<ViewMode>('table');
  const [refreshing, setRefreshing] = React.useState(false);

  const visible = React.useMemo(() => {
    let list = items.slice();
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
  }, [items, filters, keyword, sort]);

  const counts = React.useMemo(() => {
    const result = {running: 0, succeeded: 0, failed: 0};
    for (const item of items) {
      if (ACTIVE_STATUSES.includes(item.status)) result.running += 1;
      else if (item.status === 'succeeded') result.succeeded += 1;
      else if (item.status === 'failed') result.failed += 1;
    }
    return result;
  }, [items]);

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
    visible.length === items.length
      ? `${items.length} 个工作项`
      : `${visible.length} / ${items.length} 个工作项`;

  const refresh = async () => {
    setRefreshing(true);
    try {
      await reloadItems();
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div>
      <KpiCards
        total={items.length}
        running={counts.running}
        succeeded={counts.succeeded}
        failed={counts.failed}
        mappings={mappings.length}
      />

      <Card className="overflow-hidden">
        {/* 工具栏：搜索 / 筛选 / 视图切换 */}
        <div className="flex flex-col gap-3 border-b border-border px-4 py-3 lg:flex-row lg:items-center">
          <div className="relative min-w-0 flex-1 lg:max-w-[260px]">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-faint" />
            <Input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="搜索标题 / 项目 / 模型…"
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
                {value: 'email', label: '邮件'},
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
            <Button size="icon-sm" variant="outline" onClick={() => void refresh()} title="刷新">
              <RefreshCw className={cn(refreshing && 'animate-spin')} />
            </Button>
          </div>
        </div>

        {items.length === 0 ? (
          <EmptyState
            title="还没有任何工作项"
            description="点右上角「新建任务」手动录入需求或 Bug；邮件、TAPD 等外部来源在「数据源」Tab 自动接入。"
            action={
              <Button className="mt-2" onClick={() => openNewTask()}>
                新建任务
              </Button>
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
            items={visible}
            sort={sort}
            onSort={toggleSort}
            selections={selections}
            onSelect={setSelection}
            onAction={(action, id) => void runItemAction(action, id)}
            onOpen={(id) => void openReport(id)}
            mappings={mappings}
            models={models}
            backends={backends}
          />
        ) : (
          <div className="grid gap-3 bg-[#fafafd] p-4 sm:grid-cols-2 xl:grid-cols-3">
            {visible.map((item) => (
              <TaskCard
                key={item.id}
                item={item}
                selection={selections[item.id] ?? {}}
                onSelect={setSelection}
                onAction={(action, id) => void runItemAction(action, id)}
                onOpen={(id) => void openReport(id)}
                mappings={mappings}
                models={models}
                backends={backends}
              />
            ))}
          </div>
        )}
      </Card>

      <p className="mt-3 text-[11.5px] text-faint">
        提示：点击任意行查看工单详情与执行报告；apply 会在隔离 worktree 中真实改代码，执行前需二次确认。
      </p>
    </div>
  );
}