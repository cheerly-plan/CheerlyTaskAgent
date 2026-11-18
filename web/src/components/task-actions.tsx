import {Plus, RotateCcw, Square, Terminal, Trash2, FileText, Play} from 'lucide-react';

import {Button} from '@/components/ui/button';
import {SelectField} from '@/components/select-field';
import type {BackendInfo, ItemAction, ModelChoice, ProjectMapping, WorkItem} from '@/lib/api';
import {ACTIVE_STATUSES, TERMINAL_STATUSES} from '@/lib/labels';
import {effectiveDefaultModelId, modelCategory, modelHint, sortModelsByCategory} from '@/lib/utils';

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
  backends: BackendInfo[];
  /** 卡片视图里按钮换行更紧凑 */
  compact?: boolean;
  /** 首次展开模型下拉时触发后端能力探测。 */
  onProbe?: (executorId?: string) => void;
  /** 是否在操作区渲染模型下拉；表格视图把它挪到「模型」列下方。 */
  showModelSelect?: boolean;
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
  backends,
  compact,
  onProbe,
  showModelSelect = true,
}: TaskActionsProps) {
  const dryDone = item.status === 'succeeded' && item.exec_mode === 'dry-run';
  const retryable = ['failed', 'cancelled'].includes(item.status);
  const terminal = TERMINAL_STATUSES.includes(item.status) && !dryDone && !retryable;
  const running = ACTIVE_STATUSES.includes(item.status);

  const repoOptions = mappings
    .filter((mapping) => mapping.enabled)
    .map((mapping) => ({value: mapping.name, label: mapping.name}));
  // 该任务所属后端能否接受按任务指定模型；不能时只展示它 profile 里钉住的模型。
  const backend = backends.find((entry) => entry.id === item.executor);
  const canPickModel = backend?.model_selection !== false;
  const backendModels = backend?.models?.length ? backend.models : null;
  // 只认 backend 自己标记的 current（profile 钉住的那个）；目录第一条不是默认。
  const pinnedModel = !canPickModel ? backendModels?.find((model) => model.current)?.id ?? '' : '';
  const modelList = backendModels ?? models;
  // 任务一定带具体模型：已选 > 工单已有 > 后端默认/current/第一个。无空选项。
  const fallbackModel = effectiveDefaultModelId(modelList, backend?.default_model);
  const selectedModel = selection.model || item.model || fallbackModel;
  // 按「内置 / 自定义」分组展示，组内保持后端给的优先级。
  const modelOptions = sortModelsByCategory(modelList).map((model) => ({
    value: model.id,
    label: model.label ?? model.id,
    hint: modelHint(model),
    group: modelCategory(model),
  }));

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
            placeholder="请选择仓库"
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

      {showModelSelect && !terminal && !running ? (
        canPickModel && modelOptions.length > 0 ? (
          <SelectField
            size="sm"
            className={compact ? 'w-[120px]' : 'w-[132px]'}
            value={selectedModel}
            options={modelOptions}
            onChange={(value) => onSelect('model', value)}
            onOpenChange={(open) => {
              if (open) onProbe?.(item.executor);
            }}
            title="本任务使用的模型"
          />
        ) : (
          <span
            className="inline-flex items-center gap-1 rounded-md border border-dashed border-border px-2 py-1 text-[11px] text-faint"
            title={
              !canPickModel
                ? pinnedModel
                  ? `该后端不支持 --model，实际使用 profile 中钉住的模型：${pinnedModel}`
                  : '该后端不支持 --model，模型由它自己的 profile 决定'
                : '未探测到可用模型，由 CLI 自行决定'
            }
          >
            {!canPickModel
              ? `模型由 profile 决定${pinnedModel ? ` · ${pinnedModel}` : ''}`
              : '模型待探测'}
          </span>
        )
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