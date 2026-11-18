import * as React from 'react';
import {Check, Folder, Save} from 'lucide-react';

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
import {Input} from '@/components/ui/input';
import {Badge} from '@/components/ui/badge';
import {Field} from '@/components/select-field';
import type {MappingDraft, ProjectMapping, TapdWorkspace} from '@/lib/api';
import {cn} from '@/lib/utils';

interface MappingDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 传入已有映射表示编辑，null 表示新增。 */
  editing: ProjectMapping | null;
  /** 已记录的 TAPD 项目（用于显示项目名而不是 ID）。 */
  tapdProjects: TapdWorkspace[];
  onSave: (draft: MappingDraft) => void;
  onPickFolder: (apply: (path: string) => void) => void;
}

interface FormState {
  name: string;
  path: string;
  note: string;
  workspaceIds: string[];
}

const toForm = (mapping: ProjectMapping | null): FormState => ({
  name: mapping?.name ?? '',
  path: mapping?.path ?? '',
  note: mapping?.note ?? '',
  workspaceIds: mapping?.tapd_workspace_ids?.length
    ? [...mapping.tapd_workspace_ids]
    : mapping?.tapd_workspace_id ? [mapping.tapd_workspace_id] : [],
});

export function MappingDialog({
  open, onOpenChange, editing, tapdProjects, onSave, onPickFolder,
}: MappingDialogProps) {
  const [form, setForm] = React.useState<FormState>(() => toForm(editing));
  const [customId, setCustomId] = React.useState('');

  React.useEffect(() => {
    if (open) {
      setForm(toForm(editing));
      setCustomId('');
    }
  }, [open, editing]);

  const patch = (key: keyof FormState, value: string | string[]) =>
    setForm((prev) => ({...prev, [key]: value}));

  const toggleWorkspace = (id: string) =>
    setForm((prev) => ({
      ...prev,
      workspaceIds: prev.workspaceIds.includes(id)
        ? prev.workspaceIds.filter((item) => item !== id)
        : [...prev.workspaceIds, id],
    }));

  const addCustom = () => {
    const id = customId.trim();
    if (!id) return;
    setForm((prev) => ({
      ...prev,
      workspaceIds: prev.workspaceIds.includes(id) ? prev.workspaceIds : [...prev.workspaceIds, id],
    }));
    setCustomId('');
  };

  const submit = () => {
    onSave({
      name: form.name.trim(),
      path: form.path.trim(),
      note: form.note.trim(),
      tapd_workspace_ids: form.workspaceIds,
      // 兼容旧字段：写入第一个 ID，老版本仍能反查。
      tapd_workspace_id: form.workspaceIds[0] ?? '',
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[560px]">
        <DialogHeader>
          <DialogTitle>{editing ? '编辑项目映射' : '新增项目映射'}</DialogTitle>
          <DialogDescription>项目名会被用来匹配任务标题 / 描述里出现的仓库名。</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <Field label="项目名">
            <Input
              value={form.name}
              placeholder="请输入项目名"
              onChange={(event) => patch('name', event.target.value)}
            />
          </Field>
          <Field label="本地路径">
            <div className="flex gap-2">
              <Input
                className="font-mono"
                value={form.path}
                placeholder="请输入本地文件夹绝对路径"
                onChange={(event) => patch('path', event.target.value)}
              />
              <Button
                variant="outline"
                onClick={() => onPickFolder((path) => patch('path', path))}
              >
                <Folder />
                选择
              </Button>
            </div>
          </Field>
          <Field label="仓库信息 / 备注">
            <Input
              value={form.note}
              placeholder="请输入 git origin 或说明（可选）"
              onChange={(event) => patch('note', event.target.value)}
            />
          </Field>

          <Field
            label="关联 TAPD 项目（可多选）"
            hint="一个 TAPD 项目可关联多个本地仓库；关联后拉取需求/缺陷不再需要文本映射仓库。"
          >
            {tapdProjects.length === 0 && form.workspaceIds.length === 0 ? (
              <p className="text-[11.5px] text-faint">
                暂无已记录项目，可在下方直接输入项目 ID；项目名会在数据源页拉取后显示。
              </p>
            ) : (
              <div className="scrollbar-thin max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border p-1.5">
                {[...tapdProjects.map((p) => ({id: p.id, name: p.name || p.pretty_name || p.id})),
                  ...form.workspaceIds
                    .filter((id) => !tapdProjects.some((p) => p.id === id))
                    .map((id) => ({id, name: id}))].map((project) => {
                  const active = form.workspaceIds.includes(project.id);
                  return (
                    <button
                      key={project.id}
                      type="button"
                      onClick={() => toggleWorkspace(project.id)}
                      className={cn(
                        'flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] transition-colors',
                        active ? 'bg-primary/10 text-foreground' : 'hover:bg-secondary/60',
                      )}
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <span
                          className={cn(
                            'flex size-3.5 shrink-0 items-center justify-center rounded border',
                            active ? 'border-primary bg-primary text-white' : 'border-border',
                          )}
                        >
                          {active ? <Check className="size-2.5" /> : null}
                        </span>
                        <span className="min-w-0 truncate">{project.name}</span>
                      </span>
                      <Badge variant={active ? 'success' : 'info'}>{project.id}</Badge>
                    </button>
                  );
                })}
              </div>
            )}
            <div className="mt-2 flex gap-2">
              <Input
                className="font-mono"
                value={customId}
                placeholder="请输入项目 ID（不在列表里时）"
                onChange={(event) => setCustomId(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') addCustom();
                }}
              />
              <Button variant="outline" onClick={addCustom} disabled={!customId.trim()}>添加</Button>
            </div>
          </Field>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit}>
            <Save />
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
