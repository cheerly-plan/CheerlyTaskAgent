import * as React from 'react';
import {Inbox, Play, Terminal} from 'lucide-react';

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
import {Tabs, TabsContent, TabsList, TabsTrigger} from '@/components/ui/tabs';
import type {ExecMode, Kind, ModelChoice, ProjectMapping} from '@/lib/api';

export interface ManualTaskDraft {
  title: string;
  body: string;
  kind: Kind;
  repo: string;
  model: string;
}

export interface EmailTaskDraft {
  title: string;
  body: string;
  mode: ExecMode;
}

interface NewTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mappings: ProjectMapping[];
  models: ModelChoice[];
  defaultModel: string;
  onManualCreate: (draft: ManualTaskDraft, mode: 'draft' | 'dry' | 'apply') => void;
  onImportEmail: (draft: EmailTaskDraft) => void;
}

const EMPTY_MANUAL: ManualTaskDraft = {title: '', body: '', kind: 'task', repo: '', model: ''};
const EMPTY_EMAIL: EmailTaskDraft = {title: '', body: '', mode: 'dry-run'};

export function NewTaskDialog({
  open,
  onOpenChange,
  mappings,
  models,
  defaultModel,
  onManualCreate,
  onImportEmail,
}: NewTaskDialogProps) {
  const [tab, setTab] = React.useState<'manual' | 'email'>('manual');
  const [manual, setManual] = React.useState<ManualTaskDraft>(EMPTY_MANUAL);
  const [email, setEmail] = React.useState<EmailTaskDraft>(EMPTY_EMAIL);

  // 每次打开都回到「手动新建」并清空草稿，避免上一轮的残留内容被误提交
  React.useEffect(() => {
    if (open) {
      setTab('manual');
      setManual(EMPTY_MANUAL);
      setEmail(EMPTY_EMAIL);
    }
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[700px]">
        <DialogHeader>
          <DialogTitle>新建任务</DialogTitle>
          <DialogDescription>手动录入需求 / Bug，或粘贴邮件正文自动导入。</DialogDescription>
        </DialogHeader>

        <Tabs value={tab} onValueChange={(value) => setTab(value as 'manual' | 'email')}>
          <TabsList className="mx-5 mt-4 flex h-auto justify-start gap-5 rounded-none border-b border-border bg-transparent p-0">
            {[
              {value: 'manual', label: '手动新建'},
              {value: 'email', label: '邮件导入'},
            ].map((entry) => (
              <TabsTrigger
                key={entry.value}
                value={entry.value}
                className="rounded-none border-b-2 border-transparent px-0.5 pb-2.5 pt-1 text-[13px] font-medium text-foreground/60 shadow-none transition-colors hover:text-foreground focus-visible:bg-primary-muted/60 focus-visible:ring-0 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:font-semibold data-[state=active]:text-primary data-[state=active]:shadow-none"
              >
                {entry.label}
              </TabsTrigger>
            ))}
            <TabsTrigger
              value="tapd"
              disabled
              className="rounded-none border-b-2 border-transparent px-0.5 pb-2.5 pt-1 text-[13px] text-faint shadow-none"
            >
              TAPD
              <span className="ml-1 text-[10px]">预留</span>
            </TabsTrigger>
          </TabsList>

          <DialogBody>
            <TabsContent value="manual" className="m-0">
              <p className="mb-3.5 text-[11.5px] text-faint">
                不依赖邮件 / TAPD。选好项目，直接写需求或 Bug，创建后即可执行。
              </p>
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
                  <Field label="模型" hint="默认跟随全局设置。">
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
              <p className="text-[11.5px] text-faint">没有项目？到「项目映射」页添加本地文件夹。</p>
            </TabsContent>

            <TabsContent value="email" className="m-0">
              <p className="mb-3.5 text-[11.5px] text-faint">
                粘贴邮件主题 / 正文，自动识别 bug / 需求并提取 git 地址；IMAP 凭证在「系统设置」中维护。
              </p>
              <Field label="标题 / 邮件主题">
                <Textarea
                  rows={2}
                  value={email.title}
                  placeholder="【BUG】登录 500 - cheerly-plan/CheerlyAgent"
                  onChange={(event) => setEmail((prev) => ({...prev, title: event.target.value}))}
                />
              </Field>
              <Field label="描述 / 邮件正文">
                <Textarea
                  rows={5}
                  value={email.body}
                  placeholder="问题描述、复现步骤、git 地址…"
                  onChange={(event) => setEmail((prev) => ({...prev, body: event.target.value}))}
                />
              </Field>
              <Field label="执行模式">
                <SelectField
                  value={email.mode}
                  options={[
                    {value: 'dry-run', label: 'dry-run（只生成命令，不改动）'},
                    {value: 'apply', label: 'apply（真实执行，需审批）'},
                  ]}
                  onChange={(value) => setEmail((prev) => ({...prev, mode: value as ExecMode}))}
                />
              </Field>
            </TabsContent>
          </DialogBody>
        </Tabs>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          {tab === 'email' ? (
            <Button onClick={() => onImportEmail(email)}>
              <Inbox />
              导入邮件
            </Button>
          ) : (
            <>
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
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}