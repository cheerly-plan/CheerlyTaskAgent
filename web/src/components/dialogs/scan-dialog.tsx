import * as React from 'react';
import {Folder, FolderGit2, GitBranch, Plus} from 'lucide-react';
import {toast} from 'sonner';

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
import {Badge} from '@/components/ui/badge';
import {Checkbox} from '@/components/ui/checkbox';
import {EmptyState} from '@/components/status-bits';
import {cn} from '@/lib/utils';
import {endpoints, type MappingDraft, type ScanCandidate} from '@/lib/api';

interface ScanDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  path: string;
  onAdded: () => void;
}

interface Row extends ScanCandidate {
  checked: boolean;
}

/** 目录扫描结果：勾选候选文件夹后批量加入项目映射。 */
export function ScanDialog({open, onOpenChange, path, onAdded}: ScanDialogProps) {
  const [root, setRoot] = React.useState('');
  const [rows, setRows] = React.useState<Row[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setRows([]);
    setRoot(path);
    endpoints
      .scan(path)
      .then((result) => {
        if (cancelled) return;
        setRoot(result.root);
        setRows((result.candidates ?? []).map((candidate) => ({...candidate, checked: false})));
      })
      .catch((error: Error) => {
        if (cancelled) return;
        toast.error('扫描失败：' + error.message);
        onOpenChange(false);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, path, onOpenChange]);

  const allChecked = rows.length > 0 && rows.every((row) => row.checked);
  const toggleAll = () => setRows((prev) => prev.map((row) => ({...row, checked: !allChecked})));

  const addSelected = async () => {
    const chosen = rows.filter((row) => row.checked);
    if (!chosen.length) {
      toast.warning('请先勾选要加入的文件夹');
      return;
    }
    const payload: MappingDraft[] = chosen.map((row) => ({
      name: row.candidate_name || row.name,
      path: row.path,
      enabled: true,
      note: row.is_git ? `git: ${row.origin ?? ''}` : '手动扫描添加',
    }));
    setSaving(true);
    try {
      const result = await endpoints.createMappingsBulk(payload);
      let message = `已加入 ${result.added.length} 条映射`;
      if (result.failed.length) {
        message +=
          '；跳过 ' +
          result.failed.length +
          ' 条：' +
          result.failed.map((entry) => `${entry.item.name}（${entry.error}）`).join('、');
      }
      toast.success(message);
      onOpenChange(false);
      onAdded();
    } catch (error) {
      toast.error('加入失败：' + (error as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[600px]">
        <DialogHeader>
          <DialogTitle>扫描结果</DialogTitle>
          <DialogDescription className="break-all font-mono">{root || path}</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <p className="mb-2.5 text-xs text-foreground/70">
            {loading
              ? '扫描中…'
              : rows.length
                ? `发现 ${rows.length} 个文件夹（git 仓库排在前面），勾选后批量加入映射。`
                : '未发现候选文件夹'}
          </p>

          <div className="scrollbar-thin max-h-[380px] overflow-auto rounded-lg border border-border bg-card">
            {loading && !rows.length ? (
              <div className="flex items-center justify-center gap-2 py-10 text-xs text-faint">
                <span className="size-3.5 animate-spin rounded-full border-2 border-border border-t-primary" />
                正在扫描目录…
              </div>
            ) : rows.length ? (
              rows.map((row, index) => (
                <div
                  key={row.path}
                  className={cn(
                    'flex items-start gap-2.5 border-b border-[#f1f1f6] px-3 py-2.5 transition-colors last:border-b-0 hover:bg-accent/50',
                    row.checked && 'bg-accent/40',
                  )}
                >
                  <div className="pt-0.5">
                    <Checkbox
                      checked={row.checked}
                      onCheckedChange={() =>
                        setRows((prev) =>
                          prev.map((entry, position) =>
                            position === index ? {...entry, checked: !entry.checked} : entry,
                          ),
                        )
                      }
                      aria-label={`选择 ${row.candidate_name || row.name}`}
                    />
                  </div>
                  <label className="min-w-0 flex-1 cursor-pointer">
                    <span className="flex items-center gap-1.5 text-[13px] font-medium text-foreground">
                      {row.is_git ? (
                        <FolderGit2 className="size-3.5 text-success" />
                      ) : (
                        <Folder className="size-3.5 text-faint" />
                      )}
                      <span className="truncate">{row.candidate_name || row.name}</span>
                      {row.is_git ? <Badge variant="success">git</Badge> : null}
                    </span>
                    <span className="mt-0.5 block break-all font-mono text-[11px] text-faint">
                      {row.path}
                    </span>
                    {row.origin ? (
                      <span className="mt-0.5 flex items-center gap-1 break-all text-[11px] text-success-foreground">
                        <GitBranch className="size-3 shrink-0" />
                        {row.origin}
                      </span>
                    ) : null}
                  </label>
                </div>
              ))
            ) : (
              <EmptyState compact icon={Folder} title="这个目录下没有候选文件夹" />
            )}
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" className="mr-auto" onClick={toggleAll} disabled={!rows.length}>
            全选 / 全不选
          </Button>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => void addSelected()} disabled={saving}>
            <Plus />
            加入映射
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}