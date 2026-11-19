import * as React from 'react';
import {Check, CornerDownRight, ExternalLink, GitBranch, Loader2, RefreshCw} from 'lucide-react';

import {Button} from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
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
  modelHint,
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
  /** 本次拉取方式：false=增量，true=全量。仅用于展示文案。 */
  force: boolean;
  /** 正在拉取预览。 */
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

/** TAPD 同步预览弹窗：展示新增/更新条目，统一配置执行端/模型后选择性导入。 */
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

  const items = React.useMemo(() => orderForTreeView(preview?.items ?? []), [preview]);
  // 只有「新增 / 更新」可选；unchanged 仅作提示。
  const actionable = React.useMemo(() => items.filter((i) => i.status !== 'unchanged'), [items]);
  // 父需求不在本次预览结果里（父未建单 / 已被过滤）的子需求：导入后会成为孤儿。
  const refs = React.useMemo(() => new Set(items.map((i) => i.source_ref)), [items]);
  const parentTitleByRef = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const item of items) {
      if (item.source_ref) map.set(item.source_ref, item.title);
    }
    return map;
  }, [items]);

  // 父子索引：parent -> children；child -> parent（只收本次预览里可选的条目）。
  const selectableRefs = React.useMemo(
    () => new Set(actionable.map((i) => i.source_ref)),
    [actionable],
  );
  const childrenByParent = React.useMemo(() => {
    const map = new Map<string, string[]>();
    for (const item of actionable) {
      const pref = item.parent_ref;
      if (!pref || pref === item.source_ref) continue;
      map.set(pref, [...(map.get(pref) ?? []), item.source_ref]);
    }
    return map;
  }, [actionable]);

  // 每次打开预览：默认全选可操作项，并同步一次顶部配置为当前系统默认。
  React.useEffect(() => {
    if (!open) return;
    setSelected(new Set(actionable.map((i) => i.source_ref)));
    setExecutor(defaultExecutor);
    setModel(defaultModel);
    setExecMode(defaultExecMode);
  }, [open, actionable, defaultExecutor, defaultModel, defaultExecMode]);

  /**
   * 勾选联动：
   * - 勾上父需求 → 连带勾上它的全部子需求（否则父建了、子却没建，树会断开）
   * - 取消父需求 → 连带取消子需求
   * - 取消某个子需求 → 父保持勾选（父本身仍要导入）
   * 父不可选（无变化）时，子仍可单独勾选。
   */
  const toggle = (ref: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      const children = childrenByParent.get(ref) ?? [];
      if (next.has(ref)) {
        next.delete(ref);
        for (const child of children) next.delete(child);
      } else {
        next.add(ref);
        for (const child of children) next.add(child);
      }
      return next;
    });

  /** 父需求的勾选子统计（用于行尾「n/m 子需求」与半选态）。 */
  const childStats = (ref: string) => {
    const children = childrenByParent.get(ref) ?? [];
    const picked = children.filter((child) => selected.has(child)).length;
    return {total: children.length, picked, all: children.length > 0 && picked === children.length};
  };

  /** 单独切换某个父需求下的全部子需求。 */
  const toggleChildren = (ref: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      const children = childrenByParent.get(ref) ?? [];
      const allPicked = children.length > 0 && children.every((child) => next.has(child));
      for (const child of children) {
        if (allPicked) next.delete(child);
        else next.add(child);
      }
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
    hint: modelHint(m),
    group: modelCategory(m),
  }));
  const effectiveModel = effectiveDefaultModelId(activeModels, model);

  const counts = React.useMemo(() => ({
    new: items.filter((i) => i.status === 'new').length,
    changed: items.filter((i) => i.status === 'changed').length,
    unchanged: items.filter((i) => i.status === 'unchanged').length,
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
          // 每条沿用预览推断的仓库；顶部未强制指定时交给后端自动映射逻辑。
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
          <DialogDescription>
            {force ? '全量重拉：忽略增量游标，列出全部工单。' : '增量同步：只列出上次同步后修改的工单。'}
            这里只预览变更，勾选并配置后才会写入任务中心。
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
          {/* 顶部统一配置：对所有勾选条目生效 */}
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
              ) : (
                <div className="flex h-8 items-center rounded-md border border-dashed border-border px-2 text-[11px] text-faint">
                  由 profile 决定
                </div>
              )}
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

          {/* 统计与全选操作 */}
          <div className="mb-2 mt-4 flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5 text-[12px]">
              <Badge variant="success">新增 {counts.new}</Badge>
              <Badge variant="warning">更新 {counts.changed}</Badge>
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
                title="重新拉取预览（放弃当前勾选）"
              >
                <RefreshCw className="mr-0.5 inline size-3" />
                重新拉取
              </button>
            </div>
          </div>

          {/* 条目列表 */}
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
                const parentInPreview = Boolean(item.parent_ref) && refs.has(item.parent_ref!);
                const parentTitle = item.parent_ref ? parentTitleByRef.get(item.parent_ref) : '';
                const stats = childStats(item.source_ref);
                const hasChildren = stats.total > 0;
                // 子需求被勾选、但父需求没勾：父不会导入，子会变成孤儿。
                const parentSkipped =
                  isChild &&
                  selectableRefs.has(item.parent_ref!) &&
                  !selected.has(item.parent_ref!) &&
                  active;
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
                    {isChild ? (
                      <CornerDownRight className="mt-0.5 size-3.5 shrink-0 text-[#a9a0c4]" aria-hidden />
                    ) : null}
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5">
                        {isContainer ? (
                          <GitBranch className="size-3.5 shrink-0 text-[#6d3fc0]" aria-hidden />
                        ) : null}
                        {item.status === 'new' && <Badge variant="success">新增</Badge>}
                        {item.status === 'changed' && <Badge variant="warning">更新</Badge>}
                        {item.status === 'unchanged' && <Badge variant="muted">无变化</Badge>}
                        <Badge variant="outline">{item.kind}</Badge>
                        {item.repo_name && <Badge variant="info">{item.repo_name}</Badge>}
                        {hasChildren ? (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              toggleChildren(item.source_ref);
                            }}
                            title={stats.all ? '取消勾选全部子需求' : '勾选全部子需求'}
                            className={cn(
                              'rounded-full border px-1.5 py-0.5 text-[10.5px] transition-colors',
                              stats.all
                                ? 'border-primary/40 bg-primary/10 text-primary'
                                : 'border-border bg-secondary/70 text-foreground/70 hover:border-primary/40 hover:text-primary',
                            )}
                          >
                            {stats.picked}/{stats.total} 子需求
                          </button>
                        ) : null}
                      </span>
                      <span className="mt-1 block truncate text-[13px] text-foreground" title={item.title}>
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
                      {parentSkipped ? (
                        <span className="mt-0.5 block truncate text-[11px] text-warning-foreground">
                          父需求未勾选，本条导入后将成为独立任务
                        </span>
                      ) : null}
                      {isChild ? (
                        parentInPreview ? (
                          <span className="mt-0.5 block truncate text-[11px] text-[#6d3fc0]">
                            属于父需求：{parentTitle || item.parent_ref}
                          </span>
                        ) : (
                          <span className="mt-0.5 block truncate text-[11px] text-warning-foreground">
                            父需求未出现在本次结果中，导入后将是独立任务
                          </span>
                        )
                      ) : null}
                    </span>
                    {item.external_url && (
                      <a
                        href={item.external_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="mt-0.5 shrink-0 text-faint hover:text-primary"
                        title="在 TAPD 打开"
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
