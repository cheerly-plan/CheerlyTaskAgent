import * as React from 'react';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from '@tanstack/react-table';
import {CornerDownRight, FileText, FolderGit2, GitBranch} from 'lucide-react';

import {Table, TableBody, TableHeader, TableRow} from '@/components/ui/table';
import {Badge} from '@/components/ui/badge';
import {KindBadge, Mono, StatusBadge} from '@/components/status-bits';
import {SelectField} from '@/components/select-field';
import {TaskActions, type ActionSelection} from '@/components/task-actions';
import type {BackendInfo, ExecMode, ItemAction, ModelChoice, ProjectMapping, WorkItem} from '@/lib/api';
import {canEditConfig, shortTime, sourceLabel} from '@/lib/labels';
import {groupByParent} from '@/lib/task-tree';
import {
  cn,
  effectiveDefaultModelId,
  modelCategory,
  modelHint,
  modelMatches,
  resolvedModelLabel,
  sortModelsByCategory,
} from '@/lib/utils';

export type SortField = 'created' | 'title' | 'status' | 'source' | 'repo' | 'kind';

export interface SortState {
  field: SortField;
  direction: 'asc' | 'desc';
}

export const DEFAULT_SORT: SortState = {field: 'created', direction: 'desc'};

/** 首次点击新列时的默认方向：时间列从新到旧，文本列从 A 到 Z。 */
export const defaultDirection = (field: SortField): 'asc' | 'desc' =>
  field === 'created' ? 'desc' : 'asc';

interface TaskTableProps {
  items: WorkItem[];
  selections: Record<string, ActionSelection>;
  onSelect: (id: string, key: keyof ActionSelection, value: string) => void;
  onAction: (action: ItemAction, id: string) => void;
  onMap: (id: string, repo: string) => void;
  mappings: ProjectMapping[];
  models: ModelChoice[];
  backends: BackendInfo[];
  sort: SortState;
  onProbe?: (executorId?: string) => void;
  onSort: (field: SortField) => void;
  /** 滚动容器 class（长列表限制最大高度）。 */
  containerClassName?: string;
}

/** 项目列：仓库下拉，选中即映射（替代原操作列的「映射」按钮）。 */
function RepoCell({
  item,
  mappings,
  onMap,
}: {
  item: WorkItem;
  mappings: ProjectMapping[];
  onMap: (id: string, repo: string) => void;
}) {
  // 父容器不执行，项目列不适用。
  if (item.is_container) return <span className="text-xs text-faint">—</span>;
  const options = mappings
    .filter((mapping) => mapping.enabled)
    .map((mapping) => ({value: mapping.name, label: mapping.name}));
  // 已进入执行流程（排队/执行中/已结束）的任务只读展示，配置锁定。
  if (!canEditConfig(item) || !options.length) {
    return item.repo ? (
      <Badge variant="outline" className="gap-1">
        <FolderGit2 />
        {item.repo}
      </Badge>
    ) : (
      <span className="text-xs text-faint">未映射</span>
    );
  }
  return (
    <SelectField
      size="sm"
      className="w-[120px]"
      value={item.repo ?? ''}
      placeholder="选择仓库"
      options={options}
      onChange={(value) => onMap(item.id, value)}
      title="映射仓库"
    />
  );
}

/** 模式列：执行方式下拉，选中写入 selection.mode，供「执行」按钮使用。 */
function ModeCell({
  item,
  value,
  onSelect,
}: {
  item: WorkItem;
  value?: string;
  onSelect: (value: string) => void;
}) {
  const mode = (value || item.exec_mode || 'dry-run') as ExecMode;
  const options: {value: ExecMode; label: string}[] = [
    {value: 'dry-run', label: 'dry-run'},
    {value: 'apply', label: 'apply'},
  ];
  if (item.is_container) return <span className="text-xs text-faint">—</span>;
  if (!canEditConfig(item)) return <Mono>{mode}</Mono>;
  return (
    <SelectField
      size="sm"
      className="w-[92px]"
      value={mode}
      options={options}
      onChange={onSelect}
      title="执行方式（决定「执行」按钮的动作）"
    />
  );
}

/** 模型单元格：模型名 + 就地选择框（不可选的后端只读展示 profile 钉住的模型）。 */
function ModelCell({
  item,
  backends,
  models,
  selection,
  onSelect,
  onProbe,
}: {
  item: WorkItem;
  backends: BackendInfo[];
  models: ModelChoice[];
  selection: ActionSelection;
  onSelect: (key: keyof ActionSelection, value: string) => void;
  onProbe?: (executorId?: string) => void;
}) {
  const backend = backends.find((entry) => entry.id === item.executor);
  const canPickModel = backend?.model_selection !== false;
  const modelList = backend?.models?.length ? backend.models : models;

  if (!canPickModel) {
    const pinned = backend?.models?.find((model) => model.current)?.id;
    return (
      <span
        className="text-xs text-faint"
        title={pinned ? `由 ${backend?.label ?? ''} profile 决定：${pinned}` : `由 ${backend?.label ?? ''} profile 决定`}
      >
        由 profile 决定{pinned ? ` · ${pinned}` : ''}
      </span>
    );
  }

  const fallback = effectiveDefaultModelId(modelList, backend?.default_model);
  const selected = selection.model || item.model || fallback;
  const options = sortModelsByCategory(modelList).map((model) => ({
    value: model.id,
    // 分组标题已经写明「自定义模型」，选项不再重复后缀。
    label: model.label ?? model.id,
    hint: modelHint(model),
    group: modelCategory(model),
  }));
  // 父容器不执行，模型列不适用。
  if (item.is_container) return <span className="text-xs text-faint">—</span>;
  // 只有还没执行的任务能改模型；dry-run 跑完后模型必须与报告一致，锁定展示。
  const showSelect = canEditConfig(item) && options.length > 0;

  if (!showSelect) {
    return <ModelTag item={item} backends={backends} models={models} />;
  }
  return (
    <SelectField
      size="sm"
      className="w-[140px]"
      value={selected}
      options={options}
      onChange={(value) => onSelect('model', value)}
      onOpenChange={(open) => {
        if (open) onProbe?.(item.executor);
      }}
      title="本任务使用的模型"
    />
  );
}

/** 模型名（自定义模型合并成一个紫色标签）。 */
function ModelTag({
  item,
  backends,
  models,
}: {
  item: WorkItem;
  backends: BackendInfo[];
  models: ModelChoice[];
}) {
  const backend = backends.find((entry) => entry.id === item.executor);
  const list = backend?.models?.length ? backend.models : models;
  const modelId = item.model || effectiveDefaultModelId(list, backend?.default_model);
  const custom = list.some((model) => modelMatches(model, modelId) && model.custom);
  const label = resolvedModelLabel(list, modelId);
  if (custom) {
    return (
      <Badge variant="purple" title={`${label} · 用户在 CLI 中自行配置的模型`} className="max-w-[170px]">
        <span className="truncate">{label} · 自定义</span>
      </Badge>
    );
  }
  return <Mono>{label}</Mono>;
}

export function TaskTable({
  items,
  selections,
  onSelect,
  onAction,
  onMap,
  mappings,
  models,
  backends,
  sort,
  onProbe,
  onSort,
  containerClassName,
}: TaskTableProps) {
  // 按父需求合并：同父的子需求聚在一起，父需求行（若本地有）作为分组标题。
  const groups = React.useMemo(() => groupByParent(items), [items]);

  // tanstack 的排序状态与外部 sort 双向同步（外部还负责分组顺序）。
  const sorting = React.useMemo<SortingState>(
    () => [{id: sort.field, desc: sort.direction === 'desc'}],
    [sort],
  );

  const column = createColumnHelper<WorkItem>();

  const columns = React.useMemo(
    () => [
      column.accessor('title', {
        header: '标题',
        enableSorting: true,
        cell: (info) => {
          const item = info.row.original;
          const isChild = Boolean(item.parent_ref);
          const isContainer = Boolean(item.is_container);
          return (
            <div className={cn('flex items-center gap-2.5', isChild && 'pl-4')}>
              {isChild ? (
                <CornerDownRight className="size-3.5 shrink-0 text-[#a9a0c4]" aria-hidden />
              ) : null}
              <span
                className={cn(
                  'grid size-7 shrink-0 place-items-center rounded-md',
                  isContainer ? 'bg-[#ece5fb] text-[#6d3fc0]' : 'bg-secondary text-[#7b7e92]',
                )}
              >
                {isContainer ? <GitBranch className="size-3.5" /> : <FileText className="size-3.5" />}
              </span>
              <span
                className="min-w-0 truncate font-medium text-foreground group-hover:text-primary"
                title={item.title}
              >
                {item.title}
              </span>
            </div>
          );
        },
      }),
      column.accessor('kind', {
        header: '类型',
        enableSorting: true,
        cell: (info) => <KindBadge kind={info.row.original.kind} />,
      }),
      column.accessor('source', {
        header: '来源',
        enableSorting: true,
        cell: (info) => (
          <span className="text-[12.5px] text-foreground/70">
            {sourceLabel[info.row.original.source] ?? info.row.original.source ?? '—'}
          </span>
        ),
      }),
      column.accessor('repo', {
        header: '项目',
        enableSorting: true,
        cell: (info) => {
          const item = info.row.original;
          return item.is_container ? (
            <span className="text-xs text-faint">—</span>
          ) : (
            <RepoCell item={item} mappings={mappings} onMap={onMap} />
          );
        },
      }),
      column.display({
        id: 'mode',
        header: '模式',
        cell: (info) => {
          const item = info.row.original;
          return (
            <ModeCell
              item={item}
              value={selections[item.id]?.mode}
              onSelect={(value) => onSelect(item.id, 'mode', value)}
            />
          );
        },
        size: 108,
      }),
      column.display({
        id: 'model',
        header: '模型',
        cell: (info) => {
          const item = info.row.original;
          return item.is_container ? (
            <span className="text-xs text-faint">—</span>
          ) : (
            <ModelCell
              item={item}
              backends={backends}
              models={models}
              selection={selections[item.id] ?? {}}
              onSelect={(key, value) => onSelect(item.id, key, value)}
              onProbe={onProbe}
            />
          );
        },
        size: 176,
      }),
      column.accessor('status', {
        header: '状态',
        enableSorting: true,
        cell: (info) => <StatusBadge status={info.row.original.status} />,
      }),
      column.accessor('created_at', {
        header: '创建时间',
        enableSorting: true,
        cell: (info) => (
          <span className="whitespace-nowrap text-[12px] tabular-nums text-faint">
            {shortTime(info.row.original.created_at)}
          </span>
        ),
      }),
      column.display({
        id: 'actions',
        header: '操作',
        cell: (info) => {
          const item = info.row.original;
          if (item.is_container) return null;
          return (
            <TaskActions
              item={item}
              selection={selections[item.id] ?? {}}
              onSelect={(key, value) => onSelect(item.id, key, value)}
              onAction={onAction}
              models={models}
              backends={backends}
              onProbe={onProbe}
              showModelSelect={false}
            />
          );
        },
      }),
    ],
    [column, mappings, models, backends, selections, onMap, onSelect, onAction, onProbe],
  );

  const table = useReactTable({
    data: items,
    columns,
    state: {sorting},
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    manualSorting: true,
    onSortingChange: (updater) => {
      const next = typeof updater === 'function' ? updater(sorting) : updater;
      const first = next[0];
      if (!first) return;
      onSort(first.id as SortField);
    },
  });

  const headGroups = table.getHeaderGroups();
  const leafHeaders = headGroups[headGroups.length - 1]?.headers ?? [];

  /** 行 key → 行对象，供分组标题与行内渲染复用同一份视图模型。 */
  const tableRows = table.getRowModel().rows;
  const rowById = React.useMemo(() => {
    const map = new Map(tableRows.map((row) => [row.id, row]));
    return map;
  }, [tableRows]);

  return (
    <Table className="min-w-[980px]" containerClassName={containerClassName}>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {leafHeaders.map((header) => {
            const canSort = header.column.getCanSort();
            const sorted = header.column.getIsSorted();
            return (
              <th
                key={header.id}
                aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : 'none'}
                style={{width: header.getSize() === 150 ? undefined : header.getSize()}}
                className={cn(
                  'h-9 px-2 text-left align-middle text-[11.5px] font-medium text-faint',
                  header.column.id === 'title' && 'pl-4',
                  header.column.id === 'actions' &&
                    'sticky right-0 z-20 border-l border-border/70 bg-[#fafafd] pr-4 text-right shadow-[-8px_0_12px_-10px_rgba(0,0,0,0.25)]',
                )}
              >
                {canSort ? (
                  <button
                    type="button"
                    onClick={header.column.getToggleSortingHandler()}
                    title={`按${String(header.column.columnDef.header)}排序`}
                    className="inline-flex items-center gap-1 rounded px-1 py-0.5 outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    <span className={cn('text-[9px] leading-none', sorted ? 'opacity-100' : 'opacity-35')}>
                      {sorted === 'asc' ? '▲' : sorted === 'desc' ? '▼' : '⇅'}
                    </span>
                  </button>
                ) : (
                  flexRender(header.column.columnDef.header, header.getContext())
                )}
              </th>
            );
          })}
        </TableRow>
      </TableHeader>
      <TableBody>
        {groups.map((group) => (
          <React.Fragment key={group.key || `solo-${group.items[0]?.id}`}>
            {group.key ? (
              /* 父需求分组标题：父可能不在本地（只导入了子需求），用快照标题展示。 */
              <TableRow className="group/head bg-[#faf9fd] hover:bg-[#faf9fd]">
                <td colSpan={leafHeaders.length} className="sticky left-0 z-[2] py-2 pl-4">
                  <div className="flex w-max max-w-[calc(100vw-360px)] items-center gap-2.5">
                    <span className="grid size-7 shrink-0 place-items-center rounded-md bg-[#ece5fb] text-[#6d3fc0]">
                      <GitBranch className="size-3.5" />
                    </span>
                    <span className="min-w-0 truncate font-medium text-foreground">
                      {group.parentTitle || group.key}
                    </span>
                    <span className="shrink-0 text-[11.5px] text-faint">
                      {group.items.length} 个子需求
                    </span>
                  </div>
                </td>
              </TableRow>
            ) : null}
            {group.items.map((groupItem) => {
              const row = rowById.get(groupItem.id);
              if (!row) return null;
              const isContainer = Boolean(row.original.is_container);
              return (
                <TableRow
                  key={row.id}
                  className={cn('group', isContainer && 'bg-[#faf9fd]')}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className={cn(
                        'px-2 py-2 align-middle',
                        cell.column.id === 'title' && 'pl-4',
                        cell.column.id === 'actions' &&
                          'sticky right-0 z-[1] border-l border-border/70 bg-card pr-4 shadow-[-8px_0_12px_-10px_rgba(0,0,0,0.25)] group-hover:bg-[#fafafe]',
                      )}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </TableRow>
              );
            })}
          </React.Fragment>
        ))}
      </TableBody>
    </Table>
  );
}
