import * as React from 'react';
import {Plus} from 'lucide-react';

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

interface NewTapdProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 可勾选的本地仓库映射。 */
  mappings: ProjectMapping[];
  onAdd: (project: {id: string; name: string}, repoIds: string[]) => void | Promise<void>;
}

/** 手动新增一个 TAPD 项目（名称 + ID + 管理的本地仓库），用于拉不到或临时登记的项目。 */
export function NewTapdProjectDialog({
  open,
  onOpenChange,
  mappings,
  onAdd,
}: NewTapdProjectDialogProps) {
  const [name, setName] = React.useState('');
  const [id, setId] = React.useState('');
  const [repoIds, setRepoIds] = React.useState<string[]>([]);

  React.useEffect(() => {
    if (open) {
      setName('');
      setId('');
      setRepoIds([]);
    }
  }, [open]);

  const toggleRepo = (mappingId: string) => {
    setRepoIds((prev) =>
      prev.includes(mappingId) ? prev.filter((item) => item !== mappingId) : [...prev, mappingId],
    );
  };

  const submit = () => {
    if (!name.trim() || !id.trim()) return;
    void onAdd({name: name.trim(), id: id.trim()}, repoIds);
    onOpenChange(false);
  };

  const enabledMappings = mappings.filter((m) => m.enabled);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[480px]">
        <DialogHeader>
          <DialogTitle>新增项目</DialogTitle>
          <DialogDescription>登记项目名称和项目 ID，并勾选它管理的本地仓库。</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <Field label="项目名称">
            <Input
              autoFocus
              value={name}
              placeholder="请输入项目名"
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label="项目 ID">
            <Input
              className="font-mono"
              value={id}
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
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button onClick={submit} disabled={!name.trim() || !id.trim()}>
            <Plus />
            添加
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
