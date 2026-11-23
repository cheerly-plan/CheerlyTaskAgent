import * as React from 'react';
import {Folder, Save} from 'lucide-react';

import {FloatingRepoSelect} from '@/components/floating-repo-select';
import {Button} from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {Input} from '@/components/ui/input';
import {Field} from '@/components/select-field';
import {readGitOrigin, type MappingDraft, type ProjectMapping, type TapdWorkspace} from '@/lib/api';

interface MappingDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing: ProjectMapping | null;
  tapdProjects: TapdWorkspace[];
  projectNames?: Record<string, string>;
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
  open, onOpenChange, editing, tapdProjects, projectNames = {}, onSave, onPickFolder,
}: MappingDialogProps) {
  const [form, setForm] = React.useState<FormState>(() => toForm(editing));

  React.useEffect(() => {
    if (open) {
      setForm(toForm(editing));
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

  const submit = () => {
    onSave({
      name: form.name.trim(),
      path: form.path.trim(),
      note: form.note.trim(),
      tapd_workspace_ids: form.workspaceIds,
      tapd_workspace_id: form.workspaceIds[0] ?? '',
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[560px]">
        <DialogHeader>
          <DialogTitle>{editing?.id ? '编辑项目映射' : '添加项目映射'}</DialogTitle>
        </DialogHeader>

        <DialogBody>
          <Field label="项目名">
            <Input
              value={form.name}
              onChange={(event) => patch('name', event.target.value)}
            />
          </Field>
          <Field label="本地路径">
            <div className="flex gap-2">
              <Input
                className="font-mono"
                value={form.path}
                onChange={(event) => patch('path', event.target.value)}
              />
              <Button
                variant="outline"
                onClick={() =>
                  onPickFolder((path) => {
                    patch('path', path);
                    void readGitOrigin(path).then((origin) => {
                      if (origin) {
                        setForm((prev) => (prev.note.trim() ? prev : {...prev, note: `git: ${origin}`}));
                      }
                    });
                  })
                }
              >
                <Folder />
                选择
              </Button>
            </div>
          </Field>
          <Field label="仓库信息 / 备注">
            <Input
              value={form.note}
              onChange={(event) => patch('note', event.target.value)}
            />
          </Field>

          <Field label="关联 TAPD 项目（可多选）">
            <FloatingRepoSelect
              options={[
                ...tapdProjects.map((p) => ({id: p.id, name: p.name || p.pretty_name || p.id})),
                ...form.workspaceIds
                  .filter((id) => !tapdProjects.some((p) => p.id === id))
                  .map((id) => ({id, name: projectNames[id] ?? id})),
              ]}
              selected={form.workspaceIds}
              onToggle={toggleWorkspace}
              searchable
              maxHeight={320}
              onClear={() => patch('workspaceIds', [])}
              emptyText="数据源里还没有勾选 TAPD 项目，请先在「数据源」页勾选。"
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
