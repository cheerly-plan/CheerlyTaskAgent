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
  onManualCreate,
}: NewTaskDialogProps) {
  const [manual, setManual] = React.useState<ManualTaskDraft>(EMPTY_MANUAL);

  // 每次打开都清空草稿，避免上一轮的残留内容被误提交
  React.useEffect(() => {
    if (open) setManual(EMPTY_MANUAL);
  }, [open]);

  const modelOptions = [
    {value: '', label: '默认（跟随全局）'},
    ...models.map((model) => ({
      value: model.id,
      label: model.label,
      hint: model.id === defaultModel ? '全局默认模型' : undefined,
    })),
  ];

  const repoOptions = mappings.map((mapping) => ({
    value: mapping.name,
    label: mapping.name + (mapping.enabled ? '' : '（停用）'),
    disabled: !mapping.enabled,
  }));

  const enabledBackends = backends.filter((backend) => backend.enabled);
  const backendOptions = (enabledBackends.length ? enabledBackends : backends).map((backend) => ({
    value: backend.id,
    label: backend.label,
    hint: backend.command || undefined,
  }));
  // 若默认后端已停用，回退到第一个启用项。
  const effectiveExecutor = backendOptions.some((option) => option.value === manual.executor)
    ? manual.executor
    : (backendOptions[0]?.value ?? 'codebuddy');

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
                  onChange={(value) => setManual((prev) => ({...prev, executor: value}))}
                />
              </Field>
              <Field label="模型">
                <SelectField
                  value={manual.model}
                  options={modelOptions}
                  onChange={(value) => setManual((prev) => ({...prev, model: value}))}
                />
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
