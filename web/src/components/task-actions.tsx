import * as React from 'react';
import {Plus, Square, Trash2, FileText, Play} from 'lucide-react';

import {Button} from '@/components/ui/button';
import {Tooltip, TooltipContent, TooltipTrigger} from '@/components/ui/tooltip';
import {SelectField} from '@/components/select-field';
import type {BackendInfo, ItemAction, ModelChoice, WorkItem} from '@/lib/api';
import {ACTIVE_STATUSES, TERMINAL_STATUSES, canEditConfig} from '@/lib/labels';
import {effectiveDefaultModelId, modelCategory, sortModelsByCategory} from '@/lib/utils';

export interface ActionSelection {
  map?: string;
  model?: string;
  mode?: string;
}

interface TaskActionsProps {
  item: WorkItem;
  selection: ActionSelection;
  onSelect: (key: keyof ActionSelection, value: string) => void;
  onAction: (action: ItemAction, id: string) => void;
  models: ModelChoice[];
  backends: BackendInfo[];
  compact?: boolean;
  onProbe?: (executorId?: string) => void;
  showModelSelect?: boolean;
  container?: boolean;
}

function IconTip({label, children}: {label: string; children: React.ReactNode}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="top" className="bg-[#1f1f28] px-2.5 py-1 text-[11px] text-white">
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

export function TaskActions({
  item,
  selection,
  onSelect,
  onAction,
  models,
  backends,
  compact,
  onProbe,
  showModelSelect = true,
  container = false,
}: TaskActionsProps) {
  const dryDone = item.status === 'succeeded' && item.exec_mode === 'dry-run';
  const retryable = ['failed', 'cancelled'].includes(item.status);
  const terminal = TERMINAL_STATUSES.includes(item.status) && !dryDone && !retryable;
  const running = ACTIVE_STATUSES.includes(item.status);

  const backend = backends.find((entry) => entry.id === item.executor);
  const canPickModel = backend?.model_selection !== false;
  const backendModels = backend?.models?.length ? backend.models : null;
  const pinnedModel = !canPickModel ? backendModels?.find((model) => model.current)?.id ?? '' : '';
  const modelList = backendModels ?? models;
  const fallbackModel = effectiveDefaultModelId(modelList, backend?.default_model);
  const selectedModel = selection.model || item.model || fallbackModel;
  const modelOptions = sortModelsByCategory(modelList).map((model) => ({
    value: model.id,
    label: model.label ?? model.id,
    group: modelCategory(model),
  }));

  return (
    <div
      className="flex flex-nowrap items-center justify-end gap-1.5 whitespace-nowrap"
      onClick={(event) => event.stopPropagation()}
    >
      {container ? null : dryDone ? (
        <IconTip label="执行 apply">
          <Button
            size="icon-sm"
            onClick={() => onAction('apply', item.id)}
            aria-label="沿用本次 dry-run 的项目与模型执行 apply（会在隔离 worktree 改代码，需二次确认）"
          >
            <Play />
          </Button>
        </IconTip>
      ) : !terminal && !running ? (
        <IconTip label="执行">
          <Button variant="outline" size="icon-sm" onClick={() => onAction('exec', item.id)} aria-label="按模式列选择的方式执行">
            <Play color="black" />
          </Button>
        </IconTip>
      ) : null}

      {running ? (
        <IconTip label="取消">
          <Button size="icon-sm" variant="outline" onClick={() => onAction('cancel', item.id)} aria-label="取消执行">
            <Square />
          </Button>
        </IconTip>
      ) : null}

      {showModelSelect && canEditConfig(item) ? (
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
          />
        ) : (
          <span
            className="inline-flex items-center gap-1 rounded-md border border-dashed border-border px-2 py-1 text-[11px] text-faint"
          >
            {!canPickModel
              ? `模型由 profile 决定${pinnedModel ? ` · ${pinnedModel}` : ''}`
              : '模型待探测'}
          </span>
        )
      ) : null}

      {item.report_path ? (
        <IconTip label="报告">
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => onAction('report', item.id)}
            aria-label="查看执行报告"
            className="text-primary hover:bg-accent"
          >
            <FileText />
          </Button>
        </IconTip>
      ) : null}

      <IconTip label="删除">
        <Button
          size="icon-sm"
          variant="danger"
          onClick={() => onAction('delete', item.id)}
          aria-label="删除该工单"
        >
          <Trash2 />
        </Button>
      </IconTip>
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