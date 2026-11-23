import * as React from 'react';
import {Bot, GitBranch} from 'lucide-react';

import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Checkbox} from '@/components/ui/checkbox';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type {WorkItem} from '@/lib/api';
import {kindLabel, statusLabel} from '@/lib/labels';
import {cn} from '@/lib/utils';

interface AutoRunDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: WorkItem[];
  onConfirm: (ids: string[]) => void;
}

/**
 * 任务中心「自动执行」选择器：只列出已映射仓库、可执行的 READY 任务。
 * 默认全部勾选，用户可以按需取消；结果不会合并，统一进「我的待办」。
 */
export function AutoRunDialog({open, onOpenChange, items, onConfirm}: AutoRunDialogProps) {
  const [selected, setSelected] = React.useState<Set<string>>(new Set());

  React.useEffect(() => {
    if (open) setSelected(new Set(items.map((item) => item.id)));
  }, [open, items]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allSelected = items.length > 0 && selected.size === items.length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Bot className="size-4 text-primary" />
            选择要自动执行的任务
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          {items.length === 0 ? (
            <p className="py-8 text-center text-xs text-faint">
              没有可自动执行的任务（需要已映射仓库，或 TAPD 项目已绑定候选仓库）。
            </p>
          ) : (
            <>
              <div className="mb-2 flex items-center justify-between">
                <label className="flex items-center gap-2 text-[11.5px] text-foreground/70">
                  <Checkbox
                    checked={allSelected}
                    onCheckedChange={(checked) =>
                      setSelected(checked === true ? new Set(items.map((item) => item.id)) : new Set())
                    }
                  />
                  全选
                </label>
                <span className="text-[11.5px] text-faint">
                  已选 {selected.size} / {items.length}
                </span>
              </div>
              <div className="max-h-[46vh] overflow-y-auto rounded-lg border border-border bg-card">
                {items.map((item) => {
                  const isSelected = selected.has(item.id);
                  return (
                    <label
                      key={item.id}
                      className={cn(
                        'flex cursor-pointer items-start gap-3 border-b border-[#f1f1f6] px-3 py-2.5 transition-colors last:border-b-0 hover:bg-accent/50',
                        isSelected && 'bg-accent/40',
                      )}
                    >
                      <span className="flex h-[18px] shrink-0 items-center">
                        <Checkbox
                          checked={isSelected}
                          onCheckedChange={() => toggle(item.id)}
                          aria-label={`选择 ${item.title}`}
                        />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[12.5px] font-medium text-foreground">
                          {item.title}
                        </span>
                        <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-faint">
                          <Badge variant="outline">{kindLabel[item.kind] ?? item.kind}</Badge>
                          <Badge variant="secondary">{statusLabel[item.status] ?? item.status}</Badge>
                          <span className="inline-flex items-center gap-1">
                            <GitBranch className="size-3" />
                            {item.repo || '未映射'}
                          </span>
                          {item.source === 'tapd' && item.source_ref ? (
                            <span className="font-mono">{item.source_ref}</span>
                          ) : null}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={selected.size === 0}
            onClick={() => {
              onConfirm([...selected]);
              onOpenChange(false);
            }}
          >
            <Bot />
            自动执行（{selected.size}）
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}