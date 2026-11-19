import * as React from 'react';
import {ArrowDown, ArrowUp, ChevronsUpDown, CornerDownRight, FileText, FolderGit2, GitBranch} from 'lucide-react';

import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table';
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

/** 可排序表头：点击切换升/降序，键盘可达（button + aria-sort）。 */
function SortableHead({
  label,
  field,
  sort,
  onSort,
  className,
}: {
  label: string;
  field: SortField;
  sort: SortState;
  onSort: (field: SortField) => void;
  className?: string;
}) {
  const active = sort.field === field;
  const Icon = active ? (sort.direction === 'asc' ? ArrowUp : ArrowDown) : ChevronsUpDown;
  return (
    <TableHead
      aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={className}
    >
      <button
        type="button"
        onClick={() => onSort(field)}
        title={`按${label}排序`}
        className={cn(
          'group/sort -mx-1 inline-flex items-center gap-1 rounded px-1 py-0.5 outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40',
          active && 'text-foreground',
        )}
      >
        {label}
        <Icon
          className={cn(
            'size-3 shrink-0 transition-opacity',
            active ? 'opacity-100' : 'opacity-0 group-hover/sort:opacity-60',
          )}
        />
      </button>
    </TableHead>
  );
}

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

  return (
    <Table className="min-w-[980px]" containerClassName={containerClassName}>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <SortableHead label="标题" field="title" sort={sort} onSort={onSort} className="pl-4" />
          <SortableHead label="类型" field="kind" sort={sort} onSort={onSort} />
          <SortableHead label="来源" field="source" sort={sort} onSort={onSort} />
          <SortableHead label="项目" field="repo" sort={sort} onSort={onSort} />
          <TableHead className="w-[92px]">模式</TableHead>
          <TableHead>模型</TableHead>
          <SortableHead label="状态" field="status" sort={sort} onSort={onSort} />
          <SortableHead label="创建时间" field="created" sort={sort} onSort={onSort} />
          <TableHead className="sticky right-0 z-20 border-l border-border/70 bg-[#fafafd] pr-4 text-right shadow-[-8px_0_12px_-10px_rgba(0,0,0,0.25)]">
            操作
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {groups.map((group) => (
          <React.Fragment key={group.key || `solo-${group.items[0]?.id}`}>
            {group.key ? (
              /* 父需求分组标题：父可能不在本地（只导入了子需求），用快照标题展示。 */
              <TableRow className="group/head bg-[#faf9fd] hover:bg-[#faf9fd]">
                {/* 分组标题 sticky 在左侧：表格横向滚动时标题不会跟着滚走、也不会被裁成半截。 */}
                <TableCell
                  colSpan={9}
                  className="sticky left-0 z-[2] py-2 pl-4"
                >
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
                </TableCell>
              </TableRow>
            ) : null}
            {group.items.map((item) => {
          const isChild = Boolean(item.parent_ref);
          const isContainer = Boolean(item.is_container);
          return (
          <TableRow key={item.id} className={cn('group', isContainer && 'bg-[#faf9fd]')}>
            <TableCell className={cn('pl-4', isChild && 'pl-8')}>
              <div className="flex items-center gap-2.5">
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
                <div
                  className="min-w-0 truncate font-medium text-foreground group-hover:text-primary"
                  title={item.title}
                >
                  {item.title}
                </div>
              </div>
            </TableCell>
            {isContainer ? (
              /* 父需求：展示基本信息，但没有仓库/模式/模型/操作（它本身不执行）。 */
              <>
                <TableCell>
                  <KindBadge kind={item.kind} />
                </TableCell>
                <TableCell>
                  <span className="text-[12.5px] text-foreground/70">
                    {sourceLabel[item.source] ?? item.source ?? '—'}
                  </span>
                </TableCell>
                <TableCell>
                  <span className="text-xs text-faint">—</span>
                </TableCell>
                <TableCell className="w-[92px]">
                  <span className="text-xs text-faint">—</span>
                </TableCell>
                <TableCell>
                  <span className="text-xs text-faint">—</span>
                </TableCell>
                <TableCell>
                  <StatusBadge status={item.status} />
                </TableCell>
                <TableCell>
                  <span className="whitespace-nowrap text-[12px] tabular-nums text-faint">
                    {shortTime(item.created_at)}
                  </span>
                </TableCell>
                <TableCell className="sticky right-0 z-[1] border-l border-border/70 bg-card pr-4 shadow-[-8px_0_12px_-10px_rgba(0,0,0,0.25)]" />
              </>
            ) : (
              <>
                <TableCell>
                  <KindBadge kind={item.kind} />
                </TableCell>
                <TableCell>
                  <span className="text-[12.5px] text-foreground/70">
                    {sourceLabel[item.source] ?? item.source ?? '—'}
                  </span>
                </TableCell>
                <TableCell>
                  <RepoCell item={item} mappings={mappings} onMap={onMap} />
                </TableCell>
                <TableCell className="w-[92px]">
                  <ModeCell
                    item={item}
                    value={selections[item.id]?.mode}
                    onSelect={(value) => onSelect(item.id, 'mode', value)}
                  />
                </TableCell>
                <TableCell>
                  <ModelCell
                    item={item}
                    backends={backends}
                    models={models}
                    selection={selections[item.id] ?? {}}
                    onSelect={(key, value) => onSelect(item.id, key, value)}
                    onProbe={onProbe}
                  />
                </TableCell>
                <TableCell>
                  <StatusBadge status={item.status} />
                </TableCell>
                <TableCell>
                  <span className="whitespace-nowrap text-[12px] tabular-nums text-faint">
                    {shortTime(item.created_at)}
                  </span>
                </TableCell>
                <TableCell className="sticky right-0 z-[1] border-l border-border/70 bg-card pr-4 shadow-[-8px_0_12px_-10px_rgba(0,0,0,0.25)] group-hover:bg-[#fafafe]">
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
                </TableCell>
              </>
            )}
          </TableRow>
            );
            })}
          </React.Fragment>
        ))}
      </TableBody>
    </Table>
  );
}