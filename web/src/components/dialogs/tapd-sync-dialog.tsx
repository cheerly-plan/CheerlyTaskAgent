import * as React from 'react';
import {Check, ExternalLink, GitBranch, Loader2, RefreshCw} from 'lucide-react';

import {Button} from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {Badge} from '@/components/ui/badge';
import {Field, SelectField} from '@/components/select-field';
import {
  cn,
  effectiveDefaultModelId,
  modelCategory,
  sortModelsByCategory,
} from '@/lib/utils';
import type {
  BackendInfo,
  ExecMode,
  ModelChoice,
  TapdImportSelection,
  TapdPreviewResult,
} from '@/lib/api';
import {orderForTreeView} from '@/lib/task-tree';

interface TapdSyncDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  force: boolean;
  loading: boolean;
  preview: TapdPreviewResult | null;
  backends: BackendInfo[];
  models: ModelChoice[];
  defaultExecutor: string;
  defaultModel: string;
  defaultExecMode: ExecMode;
  onRefresh: () => void;
  onConfirm: (selections: TapdImportSelection[]) => Promise<void>;
}

const CHANGE_LABEL: Record<string, string> = {
  title: '标题',
  description: '描述',
};

export function TapdSyncDialog({
  open,
  onOpenChange,
  force,
  loading,
  preview,
  backends,
  models,
  defaultExecutor,
  defaultModel,
  defaultExecMode,
  onRefresh,
  onConfirm,
}: TapdSyncDialogProps) {
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [executor, setExecutor] = React.useState(defaultExecutor);
  const [model, setModel] = React.useState(defaultModel);
  const [execMode, setExecMode] = React.useState<ExecMode>(defaultExecMode);
  const [submitting, setSubmitting] = React.useState(false);

  const allItems = React.useMemo(() => preview?.items ?? [], [preview]);
  const items = React.useMemo(
    () => orderForTreeView(allItems.filter((i) => !i.is_container)),
    [allItems],
  );
  const containerTitleByRef = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const item of allItems) {
      if (item.is_container && item.source_ref) map.set(item.source_ref, item.title);
    }
    return map;
  }, [allItems]);
  const actionable = React.useMemo(() => items.filter((i) => i.status !== 'unchanged'), [items]);
  const parentTitleByRef = React.useMemo(() => {
    const map = new Map<string, string>(containerTitleByRef);
    for (const item of allItems) {
      if (item.source_ref) map.set(item.source_ref, item.title);
    }
    return map;
  }, [allItems, containerTitleByRef]);

  React.useEffect(() => {
    if (!open) return;
    setSelected(new Set(actionable.map((i) => i.source_ref)));
    setExecutor(defaultExecutor);
    setModel(defaultModel);
    setExecMode(defaultExecMode);
  }, [open, actionable, defaultExecutor, defaultModel, defaultExecMode]);

  const toggle = (ref: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(ref)) next.delete(ref);
      else next.add(ref);
      return next;
    });

  const backendOptions = backends
    .filter((b) => b.enabled)
    .map((b) => ({value: b.id, label: b.label || b.id}));
  const activeBackend = backends.find((b) => b.id === executor);
  const canPickModel = activeBackend?.model_selection !== false;
  const activeModels = activeBackend?.models?.length ? activeBackend.models : models;
  const modelOptions = sortModelsByCategory(activeModels).map((m) => ({
    value: m.id,
    label: m.label,
    group: modelCategory(m),
  }));
  const effectiveModel = effectiveDefaultModelId(activeModels, model);

  const counts = React.useMemo(() => ({
    new: items.filter((i) => i.status === 'new' && !i.archived).length,
    changed: items.filter((i) => i.status === 'changed').length,
    unchanged: items.filter((i) => i.status === 'unchanged').length,
    archived: items.filter((i) => i.archived).length,
  }), [items]);

  const confirm = async () => {
    if (selected.size === 0) return;
    setSubmitting(true);
    try {
      const selections: TapdImportSelection[] = actionable
        .filter((i) => selected.has(i.source_ref))
        .map((i) => ({
          source_ref: i.source_ref,
          executor: executor || null,
          model: canPickModel ? (effectiveModel || null) : null,
          exec_mode: execMode,
          repo_name: i.repo_name || undefined,
        }));
      await onConfirm(selections);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[760px]">
        <DialogHeader>
          <DialogTitle>同步 TAPD 需求</DialogTitle>
        </DialogHeader>

        <DialogBody>
          <div className="grid grid-cols-3 gap-3 rounded-lg border border-border bg-[#fbfbfd] p-3">
            <Field label="执行端" className="mb-0">
              <SelectField
                size="sm"
                value={executor}
                options={backendOptions}
                onChange={setExecutor}
              />
            </Field>
            <Field label="模型" className="mb-0">
              {canPickModel ? (
                <SelectField
                  size="sm"
                  value={effectiveModel}
                  options={modelOptions}
                  onChange={setModel}
                />
              ) : null}
            </Field>
            <Field label="执行模式" className="mb-0">
              <SelectField
                size="sm"
                value={execMode}
                options={[
                  {value: 'dry-run', label: 'dry-run（不改动）'},
                  {value: 'apply', label: 'apply（真实执行）'},
                ]}
                onChange={(v) => setExecMode(v as ExecMode)}
              />
            </Field>
          </div>

          <div className="mb-2 mt-4 flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5 text-[12px]">
              <Badge variant="success">新增 {counts.new}</Badge>
              <Badge variant="warning">更新 {counts.changed}</Badge>
              {counts.archived > 0 && <Badge variant="muted">归档 {counts.archived}</Badge>}
              {counts.unchanged > 0 && <Badge variant="muted">无变化 {counts.unchanged}</Badge>}
            </div>
            <div className="ml-auto flex items-center gap-2 text-[11px]">
              <button
                type="button"
                className="text-primary hover:underline"
                onClick={() => setSelected(new Set(actionable.map((i) => i.source_ref)))}
              >
                全选
              </button>
              <span className="text-faint">·</span>
              <button type="button" className="text-primary hover:underline" onClick={() => setSelected(new Set())}>
                清空
              </button>
              <span className="text-faint">·</span>
              <button
                type="button"
                className="text-primary hover:underline"
                onClick={onRefresh}
              >
                <RefreshCw className="mr-0.5 inline size-3" />
                重新拉取
              </button>
            </div>
          </div>

          {loading ? (
            <div className="flex h-40 flex-col items-center justify-center gap-2 text-[12px] text-faint">
              <Loader2 className="size-5 animate-spin" />
              正在从 TAPD 拉取并比对本地任务…
            </div>
          ) : items.length === 0 ? (
            <div className="flex h-28 items-center justify-center rounded-lg border border-dashed border-border bg-[#fbfbfd] px-4 text-center text-[11.5px] text-faint">
              {force ? '全量拉取没有返回任何工单' : '没有新变更，本地已是最新'}
            </div>
          ) : (
            <div className="scrollbar-thin max-h-[min(48vh,400px)] space-y-1.5 overflow-y-auto pr-1">
              {items.map((item) => {
                const canSelect = item.status !== 'unchanged';
                const active = selected.has(item.source_ref);
                const isChild = Boolean(item.parent_ref);
                const isContainer = Boolean(item.is_container);
                const parentTitle = item.parent_ref ? parentTitleByRef.get(item.parent_ref) : '';
                return (
                  <button
                    key={item.source_ref}
                    type="button"
                    disabled={!canSelect}
                    onClick={() => canSelect && toggle(item.source_ref)}
                    className={cn(
                      'flex w-full items-start gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors',
                      !canSelect && 'cursor-default opacity-60',
                      isContainer && 'border-[#e3d9f8] bg-[#faf9fd]',
                      active ? 'border-primary bg-primary/5' : 'border-border hover:bg-secondary/60',
                    )}
                  >
                    <span
                      className={cn(
                        'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border',
                        active ? 'border-primary bg-primary text-white' : 'border-border',
                      )}
                    >
                      {active && <Check className="size-3" />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5">
                        {isContainer ? (
                          <GitBranch className="size-3.5 shrink-0 text-[#6d3fc0]" aria-hidden />
                        ) : null}
                        {item.status === 'new' && (item.archived ? (
                          <Badge variant="muted">归档</Badge>
                        ) : (
                          <Badge variant="success">新增</Badge>
                        ))}
                        {item.status === 'changed' && <Badge variant="warning">更新</Badge>}
                        {item.status === 'unchanged' && <Badge variant="muted">无变化</Badge>}
                        <Badge variant="outline">{item.kind}</Badge>
                        {item.repo_name && (
                          <Badge variant="info">{item.repo_name}</Badge>
                        )}
                        {!item.repo_name && item.route?.needs_confirm && item.route.candidates?.length ? (
                          <Badge variant="warning">
                            待选仓库（{item.route.candidates.length}）
                          </Badge>
                        ) : null}
                        {isChild ? (
                          <Badge
                            variant="purple"
                            className="max-w-[220px]"
                          >
                            <GitBranch className="size-3 shrink-0" />
                            <span className="truncate">父需求：{parentTitle || item.parent_ref}</span>
                          </Badge>
                        ) : null}
                      </span>
                      <span className="mt-1 block truncate text-[13px] text-foreground">
                        {item.title}
                      </span>
                      {item.status === 'changed' && item.changes.length > 0 && (
                        <span className="mt-0.5 block text-[11px] text-warning-foreground">
                          TAPD 已修改：{item.changes.map((c) => CHANGE_LABEL[c] ?? c).join('、')}
                        </span>
                      )}
                      <span className="mt-0.5 block truncate text-[11px] text-faint">
                        {item.source_ref} · 更新于 {item.modified || '—'}
                      </span>
                    </span>
                    {item.external_url && (
                      <a
                        href={item.external_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="mt-0.5 shrink-0 text-faint hover:text-primary"
                        aria-label="在 TAPD 打开"
                      >
                        <ExternalLink className="size-3.5" />
                      </a>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </DialogBody>

        <DialogFooter>
          <span className="mr-auto text-[11px] text-faint">已选 {selected.size} / {actionable.length} 条可操作</span>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => void confirm()} disabled={selected.size === 0 || submitting || loading}>
            {submitting && <Loader2 className="animate-spin" />}
            确认导入 {selected.size > 0 ? `(${selected.size})` : ''}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
