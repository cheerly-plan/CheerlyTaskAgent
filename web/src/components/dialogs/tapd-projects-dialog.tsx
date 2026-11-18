import * as React from 'react';
import {ArrowLeft, Pencil, Plus} from 'lucide-react';

import {FloatingRepoSelect} from '@/components/floating-repo-select';
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
import type {ProjectMapping} from '@/lib/api';
import {cn} from '@/lib/utils';

export interface TapdProjectRow {
  id: string;
  name: string;
  pretty_name?: string;
}

interface TapdProjectsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 可勾选的本地仓库映射。 */
  mappings: ProjectMapping[];
  /** 已登记的项目（来自设置里的项目缓存）。 */
  projects: TapdProjectRow[];
  /** 每个项目当前关联的本地映射 ID。 */
  reposByProject: Record<string, string[]>;
  onAdd: (project: {id: string; name: string}, repoIds: string[]) => void | Promise<void>;
  onUpdate: (
    projectId: string,
    patch: {name: string; repoIds: string[]},
  ) => void | Promise<void>;
}

type Mode = 'list' | 'create' | 'edit';

/**
 * TAPD 项目管理：一个入口里既能新增，也能编辑已登记项目的名称与仓库关联。
 * 项目 ID 是绑定键，编辑时不可改（改 ID 等于换一个项目）。
 */
export function TapdProjectsDialog({
  open,
  onOpenChange,
  mappings,
  projects,
  reposByProject,
  onAdd,
  onUpdate,
}: TapdProjectsDialogProps) {
  const [mode, setMode] = React.useState<Mode>('list');
  const [editingId, setEditingId] = React.useState('');
  const [name, setName] = React.useState('');
  const [id, setId] = React.useState('');
  const [repoIds, setRepoIds] = React.useState<string[]>([]);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      setMode(projects.length ? 'list' : 'create');
      setEditingId('');
      setName('');
      setId('');
      setRepoIds([]);
    }
  }, [open, projects.length]);

  const startCreate = () => {
    setMode('create');
    setEditingId('');
    setName('');
    setId('');
    setRepoIds([]);
  };

  const startEdit = (project: TapdProjectRow) => {
    setMode('edit');
    setEditingId(project.id);
    setName(project.name || project.pretty_name || project.id);
    setId(project.id);
    setRepoIds([...(reposByProject[project.id] ?? [])]);
  };

  const toggleRepo = (mappingId: string) => {
    setRepoIds((prev) =>
      prev.includes(mappingId) ? prev.filter((item) => item !== mappingId) : [...prev, mappingId],
    );
  };

  const submit = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      if (mode === 'edit') {
        await onUpdate(editingId, {name: name.trim(), repoIds});
        setMode('list');
      } else {
        if (!id.trim()) return;
        await onAdd({name: name.trim(), id: id.trim()}, repoIds);
        onOpenChange(false);
      }
    } finally {
      setSaving(false);
    }
  };

  const enabledMappings = mappings.filter((m) => m.enabled);
  const repoLabel = (projectId: string) => {
    const ids = reposByProject[projectId] ?? [];
    const names = enabledMappings.filter((m) => ids.includes(m.id)).map((m) => m.name);
    return names.length ? names.join('、') : '未关联仓库';
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[520px]">
        <DialogHeader>
          <DialogTitle>{mode === 'list' ? 'TAPD 项目' : mode === 'edit' ? '编辑项目' : '新增项目'}</DialogTitle>
          <DialogDescription>
            {mode === 'list'
              ? '这里管理已登记的项目：可编辑名称与关联的本地仓库，也可新增项目。'
              : mode === 'edit'
                ? '修改项目名称和它管理的本地仓库；项目 ID 是绑定键，不可修改。'
                : '登记项目名称和项目 ID，并勾选它管理的本地仓库。'}
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
          {mode === 'list' ? (
            <div className="space-y-2">
              {projects.length === 0 ? (
                <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-[12px] text-faint">
                  还没有登记项目，点下方「新增项目」开始。
                </p>
              ) : (
                <div className="scrollbar-thin max-h-[min(46vh,340px)] space-y-1.5 overflow-y-auto">
                  {projects.map((project) => (
                    <div
                      key={project.id}
                      className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[12.5px] font-medium text-foreground">
                          {project.name || project.pretty_name || project.id}
                        </div>
                        <div className="mt-0.5 flex items-center gap-2 text-[11px] text-faint">
                          <span className="font-mono">{project.id}</span>
                          <span className="truncate">{repoLabel(project.id)}</span>
                        </div>
                      </div>
                      <Button
                        size="xs"
                        variant="outline"
                        onClick={() => startEdit(project)}
                        title="编辑名称与关联仓库"
                      >
                        <Pencil />
                        编辑
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <>
              <Field label="项目名称">
                <Input
                  autoFocus
                  value={name}
                  placeholder="请输入项目名"
                  onChange={(event) => setName(event.target.value)}
                />
              </Field>
              <Field label="项目 ID" hint={mode === 'edit' ? '项目 ID 是绑定键，编辑时不可修改' : undefined}>
                <Input
                  className={cn('font-mono', mode === 'edit' && 'bg-secondary/60 text-faint')}
                  value={id}
                  disabled={mode === 'edit'}
                  placeholder="请输入项目 ID"
                  onChange={(event) => setId(event.target.value)}
                />
              </Field>
              <Field
                label="管理的本地仓库（可选）"
                hint="点开勾选；同步来的需求/缺陷会归到这些仓库。"
              >
                <FloatingRepoSelect
                  options={enabledMappings.map((m) => ({id: m.id, name: m.name, path: m.path}))}
                  selected={repoIds}
                  onToggle={toggleRepo}
                  placeholder="未关联（可留空）"
                  emptyText="暂无本地仓库映射，可先在「项目映射」页添加。"
                />
              </Field>
            </>
          )}
        </DialogBody>

        <DialogFooter>
          {mode === 'list' ? (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                关闭
              </Button>
              <Button onClick={startCreate}>
                <Plus />
                新增项目
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="outline"
                className="mr-auto"
                onClick={() => (projects.length ? setMode('list') : onOpenChange(false))}
              >
                <ArrowLeft />
                {projects.length ? '返回列表' : '取消'}
              </Button>
              <Button
                onClick={() => void submit()}
                disabled={!name.trim() || (mode === 'create' && !id.trim()) || saving}
              >
                {mode === 'edit' ? '保存修改' : '添加'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}