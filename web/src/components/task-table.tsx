import {ArrowDown, ArrowUp, ChevronsUpDown, FileText, FolderGit2} from 'lucide-react';

import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table';
import {Badge} from '@/components/ui/badge';
import {KindBadge, Mono, StatusBadge} from '@/components/status-bits';
import {TaskActions, type ActionSelection} from '@/components/task-actions';
import type {ItemAction, ModelChoice, ProjectMapping, WorkItem} from '@/lib/api';
import {shortTime, sourceLabel} from '@/lib/labels';
import {cn} from '@/lib/utils';

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
  onOpen: (id: string) => void;
  mappings: ProjectMapping[];
  models: ModelChoice[];
  sort: SortState;
  onSort: (field: SortField) => void;
}

export function TaskTable({
  items,
  selections,
  onSelect,
  onAction,
  onOpen,
  mappings,
  models,
  sort,
  onSort,
}: TaskTableProps) {
  return (
    <Table className="min-w-[980px]">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <SortableHead label="标题" field="title" sort={sort} onSort={onSort} className="pl-4" />
          <SortableHead label="类型" field="kind" sort={sort} onSort={onSort} />
          <SortableHead label="来源" field="source" sort={sort} onSort={onSort} />
          <SortableHead label="项目" field="repo" sort={sort} onSort={onSort} />
          <SortableHead label="状态" field="status" sort={sort} onSort={onSort} />
          <TableHead>模式</TableHead>
          <TableHead>模型</TableHead>
          <SortableHead label="创建时间" field="created" sort={sort} onSort={onSort} />
          <TableHead className="pr-4 text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow
            key={item.id}
            className="group cursor-pointer"
            onClick={() => onOpen(item.id)}
          >
            <TableCell className="pl-4">
              <div className="flex items-center gap-2.5">
                <span className="grid size-7 shrink-0 place-items-center rounded-md bg-secondary text-[#7b7e92]">
                  <FileText className="size-3.5" />
                </span>
                <div
                  className="min-w-0 truncate font-medium text-foreground group-hover:text-primary"
                  title={item.title}
                >
                  {item.title}
                </div>
              </div>
            </TableCell>
            <TableCell>
              <KindBadge kind={item.kind} />
            </TableCell>
            <TableCell>
              <span className="text-[12.5px] text-foreground/70">
                {sourceLabel[item.source] ?? item.source ?? '—'}
              </span>
            </TableCell>
            <TableCell>
              {item.repo ? (
                <Badge variant="outline" className="gap-1">
                  <FolderGit2 />
                  {item.repo}
                </Badge>
              ) : (
                <span className="text-xs text-faint">未映射</span>
              )}
            </TableCell>
            <TableCell>
              <StatusBadge status={item.status} />
            </TableCell>
            <TableCell>
              <Mono>{item.exec_mode || '—'}</Mono>
            </TableCell>
            <TableCell>
              {item.model ? <Mono>{item.model}</Mono> : <span className="text-xs text-faint">全局</span>}
            </TableCell>
            <TableCell>
              <span className="whitespace-nowrap text-[12px] tabular-nums text-faint">
                {shortTime(item.created_at)}
              </span>
            </TableCell>
            <TableCell className="pr-4">
              <TaskActions
                item={item}
                selection={selections[item.id] ?? {}}
                onSelect={(key, value) => onSelect(item.id, key, value)}
                onAction={onAction}
                mappings={mappings}
                models={models}
              />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}