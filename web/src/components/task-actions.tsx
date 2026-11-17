import {Plus, RotateCcw, Square, Terminal, Trash2, FileText, Play} from 'lucide-react';

import {Button} from '@/components/ui/button';
import {SelectField} from '@/components/select-field';
import type {ItemAction, ModelChoice, ProjectMapping, WorkItem} from '@/lib/api';
import {ACTIVE_STATUSES, TERMINAL_STATUSES} from '@/lib/labels';

export interface ActionSelection {
  map?: string;
  model?: string;
}

interface TaskActionsProps {
  item: WorkItem;
  selection: ActionSelection;
  onSelect: (key: keyof ActionSelection, value: string) => void;
  onAction: (action: ItemAction, id: string) => void;
  mappings: ProjectMapping[];
  models: ModelChoice[];
  /** 卡片视图里按钮换行更紧凑 */
  compact?: boolean;
}

/**
 * 状态驱动的动作集合（与原逻辑保持一致）：
 * 未映射 → 选仓库 + 映射；失败/取消 → 重试；其余可执行 → dry-run / apply；
 * 执行中 → 取消；已出报告 → 报告；始终可删除。
 */
export function TaskActions({
  item,
  selection,
  onSelect,
  onAction,
  mappings,
  models,
  compact,
}: TaskActionsProps) {
  const dryDone = item.status === 'succeeded' && item.exec_mode === 'dry-run';
  const retryable = ['failed', 'cancelled'].includes(item.status);
  const terminal = TERMINAL_STATUSES.includes(item.status) && !dryDone && !retryable;
  const running = ACTIVE_STATUSES.includes(item.status);

  const repoOptions = mappings
    .filter((mapping) => mapping.enabled)
    .map((mapping) => ({value: mapping.name, label: mapping.name}));
  const modelOptions = [
    {value: '', label: '默认（跟随全局）'},
    ...models.map((model) => ({value: model.id, label: model.label})),
  ];

  return (
    <div
      className="flex flex-wrap items-center justify-end gap-1.5"
      onClick={(event) => event.stopPropagation()}
    >
      {!item.repo ? (
        <>
          <SelectField
            size="sm"
            className="w-[124px]"
            value={selection.map ?? ''}
            placeholder="选仓库…"
            options={repoOptions}
            onChange={(value) => onSelect('map', value)}
          />
          <Button size="sm" variant="outline" onClick={() => onAction('map', item.id)}>
            映射
          </Button>
        </>
      ) : null}

      {retryable ? (
        <Button size="sm" variant="outline" onClick={() => onAction('dry', item.id)}>
          <RotateCcw />
          重试
        </Button>
      ) : !dryDone && !terminal && !running ? (
        <Button size="sm" variant="outline" onClick={() => onAction('dry', item.id)}>
          <Terminal />
          dry-run
        </Button>
      ) : null}

      {!terminal && !running ? (
        <Button size="sm" onClick={() => onAction('apply', item.id)} title="在隔离 worktree 中真实执行">
          <Play />
          apply
        </Button>
      ) : null}

      {running ? (
        <Button size="sm" variant="outline" onClick={() => onAction('cancel', item.id)}>
          <Square />
          取消
        </Button>
      ) : null}

      {!terminal && !running ? (
        <SelectField
          size="sm"
          className={compact ? 'w-[120px]' : 'w-[132px]'}
          value={selection.model ?? ''}
          options={modelOptions}
          onChange={(value) => onSelect('model', value)}
          title="本任务使用的模型"
        />
      ) : null}

      {item.report_path ? (
        <Button size="sm" variant="ghost" onClick={() => onAction('report', item.id)}>
          <FileText />
          报告
        </Button>
      ) : null}

      <Button
        size="sm"
        variant="danger"
        onClick={() => onAction('delete', item.id)}
        title="删除该工单"
        aria-label="删除该工单"
      >
        <Trash2 />
      </Button>
    </div>
  );
}

export function NewTaskButton({onClick}: {onClick: () => void}) {
  return (
    <Button onClick={onClick}>
      <Plus />
      新建任务
    </Button>
  );
}