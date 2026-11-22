import * as React from 'react';
import {Play, Terminal} from 'lucide-react';

import {Button} from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
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
  defaultExecutor?: string;
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
  }));
  const firstExecutor =
    backendOptions.find((option) => option.value === defaultExecutor)?.value ??
    backendOptions[0]?.value ??
    'codebuddy';

  React.useEffect(() => {
    if (open) {
      const firstBackend = backends.find((backend) => backend.id === firstExecutor);
      const firstModels = firstBackend?.models?.length ? firstBackend.models : models;
      const canonical = canonicalModelId(firstModels, defaultModel);
      const model = firstModels.some((item) => modelMatches(item, canonical))
        ? canonical
        : effectiveDefaultModelId(firstModels, '');
      setManual({...EMPTY_MANUAL, executor: firstExecutor, model});
    }
  }, [open, defaultModel, firstExecutor, models, backends]);

  const backendInfo = backends.find((backend) => backend.id === manual.executor);
  const canPickModel = backendInfo?.model_selection !== false;

  const backendModels = backendInfo?.models?.length ? backendInfo.models : models;
  const modelOptions = sortModelsByCategory(backendModels).map((model) => ({
    value: model.id,
    label: model.label,
    group: modelCategory(model),
  }));

  const repoOptions = mappings.map((mapping) => ({
    value: mapping.name,
    label: mapping.name + (mapping.enabled ? '' : '（停用）'),
    disabled: !mapping.enabled,
  }));

  const effectiveExecutor = backendOptions.some((option) => option.value === manual.executor)
    ? manual.executor
    : firstExecutor;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[700px]">
        <DialogHeader>
          <DialogTitle>新建任务</DialogTitle>
        </DialogHeader>

        <DialogBody>
          <div className="grid gap-x-4 sm:grid-cols-2">
            <div>
              <Field label="目标项目（来自项目映射）">
                <SelectField
                  value={manual.repo}
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
              <Field label="模型">
                {canPickModel && modelOptions.length > 0 ? (
                  <SelectField
                    value={manual.model}
                    options={modelOptions}
                    onChange={(value) => setManual((prev) => ({...prev, model: value}))}
                    onOpenChange={(open) => {
                      if (open) onProbe?.(effectiveExecutor);
                    }}
                  />
                ) : null}
              </Field>
            </div>
            <div>
              <Field label="标题">
                <Textarea
                  rows={2}
                  value={manual.title}
                  onChange={(event) => setManual((prev) => ({...prev, title: event.target.value}))}
                />
              </Field>
              <Field label="需求 / Bug 描述">
                <Textarea
                  rows={4}
                  value={manual.body}
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
