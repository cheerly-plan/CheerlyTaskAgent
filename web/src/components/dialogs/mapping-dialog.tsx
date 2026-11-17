import * as React from 'react';
import {Folder, Save} from 'lucide-react';

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
import {Field} from '@/components/select-field';
import type {MappingDraft, ProjectMapping} from '@/lib/api';

interface MappingDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 传入已有映射表示编辑，null 表示新增。 */
  editing: ProjectMapping | null;
  onSave: (draft: MappingDraft) => void;
  onPickFolder: (apply: (path: string) => void) => void;
}

interface FormState {
  name: string;
  aliases: string;
  path: string;
  note: string;
}

const toForm = (mapping: ProjectMapping | null): FormState => ({
  name: mapping?.name ?? '',
  aliases: (mapping?.aliases ?? []).join(', '),
  path: mapping?.path ?? '',
  note: mapping?.note ?? '',
});

export function MappingDialog({open, onOpenChange, editing, onSave, onPickFolder}: MappingDialogProps) {
  const [form, setForm] = React.useState<FormState>(() => toForm(editing));

  React.useEffect(() => {
    if (open) setForm(toForm(editing));
  }, [open, editing]);

  const patch = (key: keyof FormState, value: string) =>
    setForm((prev) => ({...prev, [key]: value}));

  const submit = () => {
    onSave({
      name: form.name.trim(),
      aliases: form.aliases
        .split(',')
        .map((alias) => alias.trim())
        .filter(Boolean),
      path: form.path.trim(),
      note: form.note.trim(),
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[540px]">
        <DialogHeader>
          <DialogTitle>{editing ? '编辑项目映射' : '新增项目映射'}</DialogTitle>
          <DialogDescription>项目名会被用来匹配邮件 / 任务里出现的仓库名。</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <Field label="项目名">
            <Input
              value={form.name}
              placeholder="如 CheerlyAIChat"
              onChange={(event) => patch('name', event.target.value)}
            />
          </Field>
          <Field label="别名（逗号分隔）">
            <Input
              value={form.aliases}
              placeholder="ai_chat, AI聊天"
              onChange={(event) => patch('aliases', event.target.value)}
            />
          </Field>
          <Field label="本地路径">
            <div className="flex gap-2">
              <Input
                className="font-mono"
                value={form.path}
                placeholder="本地文件夹绝对路径"
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
              placeholder="git origin 地址或说明（可选）"
              onChange={(event) => patch('note', event.target.value)}
            />
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