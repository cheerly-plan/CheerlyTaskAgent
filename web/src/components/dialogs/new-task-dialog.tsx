import * as React from 'react';
import {Play, Terminal} from 'lucide-react';

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
import {Textarea} from '@/components/ui/textarea';
import {Field, SelectField} from '@/components/select-field';
import type {BackendInfo, Kind, ModelChoice, ProjectMapping} from '@/lib/api';
import {
  canonicalModelId,
  effectiveDefaultModelId,
  modelCategory,
  modelHint,
  modelMatches,
  sortModelsByCategory,
} from '@/lib/utils';

export interface ManualTaskDraft {
  title: string;
  body: string;
  kind: Kind;
  repo: string;
  executor: string;
  model: string;
}

interface NewTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mappings: ProjectMapping[];
  models: ModelChoice[];
  defaultModel: string;
  backends: BackendInfo[];
  /** 系统设置里选的默认后端；缺失时退回列表第一个。 */
  defaultExecutor?: string;
  /** 首次展开模型下拉时探测该后端能否按任务指定模型。 */
  onProbe?: (executorId?: string) => void;
  onManualCreate: (draft: ManualTaskDraft, mode: 'draft' | 'dry' | 'apply') => void;
}

const EMPTY_MANUAL: ManualTaskDraft = {title: '', body: '', kind: 'task', repo: '', executor: 'codebuddy', model: ''};

export function NewTaskDialog({
  open,
  onOpenChange,
  mappings,
  models,
  defaultModel,
  backends,
  defaultExecutor,
  onProbe,
  onManualCreate,
}: NewTaskDialogProps) {
  const [manual, setManual] = React.useState<ManualTaskDraft>(EMPTY_MANUAL);

  const enabledBackends = backends.filter((backend) => backend.enabled);
  const backendOptions = (enabledBackends.length ? enabledBackends : backends).map((backend) => ({
    value: backend.id,
    label: backend.label,
    hint: backend.command || undefined,
  }));
  // 初始后端优先跟随系统设置的默认后端，而不是列表顺序里的第一个。
  const firstExecutor =
    backendOptions.find((option) => option.value === defaultExecutor)?.value ??
    backendOptions[0]?.value ??
    'codebuddy';

  // 每次打开都重置草稿；模型/后端预填系统设置里真正保存的值，避免歧义。
  // 已保存值若是被去重折叠的别名（如 hy4flash），归一到规范 id 才能选中。
  React.useEffect(() => {
    if (open) {
      // 回填一个具体模型：系统设置保存值 > 别名归一 > 该后端默认/current/第一个。
      // 必须用「首个后端」自己的清单：不同 CLI 的模型完全不重叠。
      const firstBackend = backends.find((backend) => backend.id === firstExecutor);
      const firstModels = firstBackend?.models?.length ? firstBackend.models : models;
      // 已保存值可能属于另一个后端（如从 codebuddy 切到 DSH）：只有命中当前
      // 后端清单才回填，否则落到该后端的默认模型，避免下拉出现「空选中」。
      const canonical = canonicalModelId(firstModels, defaultModel);
      const model = firstModels.some((item) => modelMatches(item, canonical))
        ? canonical
        : effectiveDefaultModelId(firstModels, '');
      setManual({...EMPTY_MANUAL, executor: firstExecutor, model});
    }
  }, [open, defaultModel, firstExecutor, models, backends]);

  // 该后端把模型钉在自己的 profile 里（model_selection === false）时，不给假的选项。
  const backendInfo = backends.find((backend) => backend.id === manual.executor);
  const canPickModel = backendInfo?.model_selection !== false;
  const pinnedModel = !canPickModel ? backendInfo?.models?.find((model) => model.current)?.id : undefined;

  // 模型选项必须来自「当前选中的后端」：codebuddy 与 DSH 的模型集合完全不同，
  // 混用会出现「选了 DSH 却列出 codebuddy 模型」的错配。
  const backendModels = backendInfo?.models?.length ? backendInfo.models : models;
  const modelOptions = sortModelsByCategory(backendModels).map((model) => ({
    value: model.id,
    label: model.label,
    hint: modelHint(model, modelMatches(model, defaultModel) ? '全局默认' : undefined),
    group: modelCategory(model),
  }));

  const repoOptions = mappings.map((mapping) => ({
    value: mapping.name,
    label: mapping.name + (mapping.enabled ? '' : '（停用）'),
    disabled: !mapping.enabled,
  }));

  // 若默认后端已停用，回退到第一个启用项。
  const effectiveExecutor = backendOptions.some((option) => option.value === manual.executor)
    ? manual.executor
    : firstExecutor;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[700px]">
        <DialogHeader>
          <DialogTitle>新建任务</DialogTitle>
          <DialogDescription>手动录入需求 / Bug，选好项目后即可创建并执行。</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <div className="grid gap-x-4 sm:grid-cols-2">
            <div>
              <Field label="目标项目（来自项目映射）">
                <SelectField
                  value={manual.repo}
                  placeholder="选择项目…"
                  options={repoOptions}
                  onChange={(value) => setManual((prev) => ({...prev, repo: value}))}
                />
              </Field>
              <Field label="类型">
                <SelectField
                  value={manual.kind}
                  options={[
                    {value: 'task', label: '任务 task'},
                    {value: 'feature', label: '需求 feature'},
                    {value: 'bug', label: '缺陷 bug'},
                  ]}
                  onChange={(value) => setManual((prev) => ({...prev, kind: value as Kind}))}
                />
              </Field>
              <Field label="执行后端">
                <SelectField
                  value={effectiveExecutor}
                  options={backendOptions}
                  onChange={(value) =>
                    setManual((prev) => {
                      const nextBackend = backends.find((backend) => backend.id === value);
                      const nextModels = nextBackend?.models?.length ? nextBackend.models : models;
                      const kept = canonicalModelId(nextModels, prev.model);
                      const compatible = nextModels.some((model) => modelMatches(model, kept));
                      return {
                        ...prev,
                        executor: value,
                        model: compatible ? kept : effectiveDefaultModelId(nextModels, ''),
                      };
                    })
                  }
                />
              </Field>
              <Field
                label="模型"
                hint={
                  canPickModel
                    ? undefined
                    : `该后端不支持按任务指定模型，实际使用 profile 中的模型${pinnedModel ? `（${pinnedModel}）` : ''}。`
                }
              >
                {canPickModel && modelOptions.length > 0 ? (
                  <SelectField
                    value={manual.model}
                    options={modelOptions}
                    onChange={(value) => setManual((prev) => ({...prev, model: value}))}
                    onOpenChange={(open) => {
                      if (open) onProbe?.(effectiveExecutor);
                    }}
                  />
                ) : (
                  <div className="flex h-9 items-center rounded-lg border border-dashed border-border bg-[#fbfbfd] px-3 text-xs text-faint">
                    {canPickModel ? '未探测到可用模型，由 CLI 决定' : `由 profile 决定${pinnedModel ? ` · ${pinnedModel}` : ''}`}
                  </div>
                )}
              </Field>
            </div>
            <div>
              <Field label="标题">
                <Textarea
                  rows={2}
                  value={manual.title}
                  placeholder="例如：调整 ai_task 调度台，新增顶部 Tab"
                  onChange={(event) => setManual((prev) => ({...prev, title: event.target.value}))}
                />
              </Field>
              <Field label="需求 / Bug 描述">
                <Textarea
                  rows={4}
                  value={manual.body}
                  placeholder="写清要改什么、验收标准、注意事项…"
                  onChange={(event) => setManual((prev) => ({...prev, body: event.target.value}))}
                />
              </Field>
            </div>
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button variant="secondary" onClick={() => onManualCreate(manual, 'draft')}>
            创建草稿
          </Button>
          <Button variant="outline" onClick={() => onManualCreate(manual, 'dry')}>
            <Terminal />
            创建并 dry-run
          </Button>
          <Button onClick={() => onManualCreate(manual, 'apply')}>
            <Play />
            创建并 apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
