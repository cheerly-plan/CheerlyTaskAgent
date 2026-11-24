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
import {Field, SelectField} from '@/components/select-field';
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
  delivery: string;
  mergePolicy: string;
}

const toForm = (mapping: ProjectMapping | null): FormState => ({
  name: mapping?.name ?? '',
  path: mapping?.path ?? '',
  note: mapping?.note ?? '',
  workspaceIds: mapping?.tapd_workspace_ids?.length
    ? [...mapping.tapd_workspace_ids]
    : mapping?.tapd_workspace_id ? [mapping.tapd_workspace_id] : [],
  delivery: mapping?.delivery ?? '',
  mergePolicy: mapping?.merge_policy ?? '',
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
      delivery: form.delivery,
      merge_policy: form.mergePolicy,
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

          <div className="grid grid-cols-2 gap-3">
            <Field label="交付">
              <SelectField
                value={form.delivery}
                onChange={(value) => patch('delivery', value)}
                options={[
                  {value: '', label: '跟随系统设置'},
                  {value: 'off', label: '关闭'},
                  {value: 'commit', label: '仅提交任务分支'},
                  {value: 'push', label: '提交并推送'},
                  {value: 'pr', label: '推送并创建 PR/MR'},
                ]}
              />
            </Field>
            <Field label="PR 合并">
              <SelectField
                value={form.mergePolicy}
                onChange={(value) => patch('mergePolicy', value)}
                options={[
                  {value: '', label: '跟随系统设置'},
                  {value: 'manual', label: '人工合并'},
                  {value: 'auto', label: 'CI 通过后自动合并'},
                ]}
              />
            </Field>
          </div>
          <p className="mb-3.5 -mt-1 text-[11px] leading-relaxed text-foreground/55">
            交付默认关闭；开启后会先在任务分支做过滤提交，再按模式 push / 创建 PR。
            自动合并仅在 PR 模式、CI 全部通过时生效；CI 失败或未配置时保持人工处理。
          </p>

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
