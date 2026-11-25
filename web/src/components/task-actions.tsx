import * as React from 'react';
import {createPortal} from 'react-dom';
import {MoreHorizontal, Plus, RefreshCw, Square, Trash2, FileText, GitBranch, GitMerge, Play} from 'lucide-react';

import {Button, type ButtonProps} from '@/components/ui/button';
import {Tooltip, TooltipContent, TooltipTrigger} from '@/components/ui/tooltip';
import {SelectField} from '@/components/select-field';
import type {BackendInfo, ItemAction, ModelChoice, WorkItem} from '@/lib/api';
import {ACTIVE_STATUSES, TERMINAL_STATUSES, canEditConfig} from '@/lib/labels';
import {cn, effectiveDefaultModelId, modelCategory, sortModelsByCategory} from '@/lib/utils';

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
  /** 表格操作列使用：超过该数量的按钮收进「更多操作」菜单，保持列宽固定。 */
  maxVisibleActions?: number;
}

interface ActionDef {
  key: string;
  label: string;
  ariaLabel: string;
  icon: React.ReactNode;
  onClick: () => void;
  variant?: ButtonProps['variant'];
  className?: string;
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

function InlineAction({action}: {action: ActionDef}) {
  return (
    <IconTip label={action.label}>
      <Button
        size="icon-sm"
        variant={action.variant}
        onClick={action.onClick}
        aria-label={action.ariaLabel}
        className={action.className}
      >
        {action.icon}
      </Button>
    </IconTip>
  );
}

/** 溢出的操作按钮收进下拉菜单。菜单渲染到 body，避免被表格单元格的层叠上下文遮挡。 */
function ActionMenu({actions}: {actions: ActionDef[]}) {
  const [open, setOpen] = React.useState(false);
  const [position, setPosition] = React.useState({top: 0, left: 0});
  const triggerRef = React.useRef<HTMLSpanElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);

  const close = React.useCallback(() => setOpen(false), []);

  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    // 捕获阶段监听，表格容器停止冒泡的 click 不会影响这里。
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open, close]);

  const toggle = (event: React.MouseEvent<HTMLButtonElement>) => {
    if (!open) {
      const rect = event.currentTarget.getBoundingClientRect();
      setPosition({top: rect.bottom + 4, left: rect.right});
    }
    setOpen((value) => !value);
  };

  return (
    <>
      <span ref={triggerRef} className="inline-flex">
        <IconTip label="更多操作">
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="更多操作"
            aria-expanded={open}
            onClick={toggle}
          >
            <MoreHorizontal />
          </Button>
        </IconTip>
      </span>
      {open
        ? createPortal(
            <div
              ref={menuRef}
              role="menu"
              style={{
                position: 'fixed',
                top: position.top,
                left: position.left,
                transform: 'translateX(-100%)',
              }}
              className="z-[100] min-w-[144px] rounded-lg border border-border bg-card p-1 shadow-xl"
            >
              {actions.map((action) => (
                <button
                  key={action.key}
                  type="button"
                  role="menuitem"
                  className={cn(
                    'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] outline-none transition-colors [&_svg]:size-3.5',
                    action.variant === 'danger'
                      ? 'text-destructive hover:bg-destructive-muted'
                      : 'text-foreground/80 hover:bg-accent hover:text-foreground',
                  )}
                  onClick={() => {
                    close();
                    action.onClick();
                  }}
                >
                  {action.icon}
                  <span>{action.label}</span>
                </button>
              ))}
            </div>,
            document.body,
          )
        : null}
    </>
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
  maxVisibleActions,
}: TaskActionsProps) {
  const dryDone = item.status === 'succeeded' && item.exec_mode === 'dry-run';
  const retryable = ['failed', 'cancelled'].includes(item.status);
  const terminal = TERMINAL_STATUSES.includes(item.status) && !dryDone && !retryable;
  const running = ACTIVE_STATUSES.includes(item.status);
  const mergeBlocked = item.merge_state === 'blocked';
  const verificationFailed = item.result?.verification?.ok === false
    && item.merge_state !== 'merged';

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

  const modelSelect = showModelSelect && canEditConfig(item) ? (
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
  ) : null;

  // 主要操作（执行/取消）与状态相关操作
  const leading: ActionDef[] = [];
  if (verificationFailed) {
    leading.push({
      key: 'verify',
      label: '重新验证',
      ariaLabel: '在现有 worktree 上重新运行验证（不重新执行 agent）',
      icon: <RefreshCw />,
      onClick: () => onAction('verify', item.id),
      variant: 'outline',
    });
    leading.push({
      key: 'force-merge',
      label: '强制合并',
      ariaLabel: '跳过验证门禁，把 worktree 的改动原子合并到主仓库工作区',
      icon: <GitMerge />,
      onClick: () => onAction('force-merge', item.id),
      variant: 'danger',
    });
  }
  if (mergeBlocked) {
    leading.push({
      key: 'merge',
      label: '重试合并',
      ariaLabel: '重试把隔离 worktree 的改动合并到主仓库工作区',
      icon: <GitMerge />,
      onClick: () => onAction('merge', item.id),
      variant: 'outline',
    });
  }
  if (!container) {
    if (dryDone) {
      leading.push({
        key: 'apply',
        label: '执行 apply',
        ariaLabel: '沿用本次 dry-run 的项目与模型执行 apply（会在隔离 worktree 改代码，需二次确认）',
        icon: <Play />,
        onClick: () => onAction('apply', item.id),
      });
    } else if (!terminal && !running) {
      leading.push({
        key: 'exec',
        label: '执行',
        ariaLabel: '按模式列选择的方式执行',
        icon: <Play color="black" />,
        onClick: () => onAction('exec', item.id),
        variant: 'outline',
      });
    }
  }
  const canDecompose =
    !container &&
    !running &&
    !item.is_container &&
    !(item.children_ids?.length ?? 0) &&
    item.merge_state !== 'merged' &&
    !(item.result && item.result.dry_run === false) &&
    (item.kind === 'feature' || item.kind === 'task');
  if (canDecompose) {
    leading.push({
      key: 'decompose',
      label: '拆解为子任务',
      ariaLabel: '把跨仓/多步骤需求拆成子任务（父需求变成容器，不单独执行）',
      icon: <GitBranch />,
      onClick: () => onAction('decompose', item.id),
      variant: 'outline',
    });
  }
  if (running) {
    leading.push({
      key: 'cancel',
      label: '取消',
      ariaLabel: '取消执行',
      icon: <Square />,
      onClick: () => onAction('cancel', item.id),
      variant: 'outline',
    });
  }

  // 执行后才会出现的操作（报告）与固定操作（删除）
  const trailing: ActionDef[] = [];
  if (item.report_path) {
    trailing.push({
      key: 'report',
      label: '报告',
      ariaLabel: '查看执行报告',
      icon: <FileText />,
      onClick: () => onAction('report', item.id),
      variant: 'ghost',
      className: 'text-primary hover:bg-accent',
    });
  }
  trailing.push({
    key: 'delete',
    label: '删除',
    ariaLabel: '删除该工单',
    icon: <Trash2 />,
    onClick: () => onAction('delete', item.id),
    variant: 'danger',
  });

  const allActions = [...leading, ...trailing];
  const limit = maxVisibleActions ?? 0;
  // 「…」本身也占一个按钮位：总操作数 > 3 时只留 2 个主操作 + 「…」，
  // 保证操作列里始终最多 3 个按钮。
  const overflow = limit > 0 && allActions.length > limit;
  const visibleLimit = Math.max(1, limit - (overflow ? 1 : 0));
  const rowClass = cn(
    'flex flex-nowrap items-center justify-end whitespace-nowrap',
    maxVisibleActions
      ? 'gap-1 [&_button]:!size-6 [&_button_svg]:!size-3'
      : 'gap-1.5',
  );

  if (overflow) {
    // 优先保留「执行/取消/报告/合并」，删除等低频或危险操作收进更多菜单。
    const picked = new Set<string>();
    for (const key of ['exec', 'apply', 'cancel', 'report', 'merge', 'verify', 'force-merge', 'decompose', 'delete']) {
      if (picked.size >= visibleLimit) break;
      if (allActions.some((action) => action.key === key)) picked.add(key);
    }
    for (const action of allActions) {
      if (picked.size >= visibleLimit) break;
      picked.add(action.key);
    }
    const visible = allActions.filter((action) => picked.has(action.key));
    const hidden = allActions.filter((action) => !picked.has(action.key));
    return (
      <div
        className={rowClass}
        onClick={(event) => event.stopPropagation()}
      >
        {visible.map((action) => (
          <InlineAction key={action.key} action={action} />
        ))}
        {modelSelect}
        <ActionMenu actions={hidden} />
      </div>
    );
  }

  return (
    <div
      className={rowClass}
      onClick={(event) => event.stopPropagation()}
    >
      {leading.map((action) => (
        <InlineAction key={action.key} action={action} />
      ))}
      {modelSelect}
      {trailing.map((action) => (
        <InlineAction key={action.key} action={action} />
      ))}
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
