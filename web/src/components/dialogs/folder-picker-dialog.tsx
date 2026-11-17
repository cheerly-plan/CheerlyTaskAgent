import * as React from 'react';
import {Check, ChevronRight, Folder, FolderGit2, Home, ArrowUp, Search} from 'lucide-react';
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
import {EmptyState} from '@/components/status-bits';
import {endpoints, type BrowseResult} from '@/lib/api';

interface FolderPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (path: string) => void;
}

const INITIAL: BrowseResult = {current: '', parent: null, at_root: true, dirs: []};

/** 只读目录浏览器：逐级进入子目录，选中当前路径交给调用方。 */
export function FolderPickerDialog({open, onOpenChange, onPick}: FolderPickerDialogProps) {
  const [state, setState] = React.useState<BrowseResult>(INITIAL);
  const [loading, setLoading] = React.useState(false);

  const browse = React.useCallback(async (path: string) => {
    setLoading(true);
    try {
      setState(await endpoints.browse(path));
    } catch (error) {
      toast.error('浏览失败：' + (error as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (open) void browse('');
  }, [open, browse]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[580px]">
        <DialogHeader>
          <DialogTitle>选择文件夹</DialogTitle>
          <DialogDescription>逐级点选进入子文件夹，点「选择当前文件夹」确定当前路径。</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <div className="mb-2.5 flex items-center gap-2 rounded-lg border border-border bg-[#fbfbfd] px-3 py-2">
            <Folder className="size-4 shrink-0 text-faint" />
            <code
              className="min-w-0 flex-1 truncate font-mono text-xs text-foreground/80"
              title={state.current || ''}
            >
              {state.current || '…'}
            </code>
            {loading ? (
              <span className="size-3.5 shrink-0 animate-spin rounded-full border-2 border-border border-t-primary" />
            ) : null}
          </div>

          <div className="scrollbar-thin max-h-[340px] overflow-auto rounded-lg border border-border bg-card">
            {state.dirs.length ? (
              state.dirs.map((dir) => (
                <button
                  key={dir.path}
                  type="button"
                  onClick={() => void browse(dir.path)}
                  className="flex w-full items-center gap-2.5 border-b border-[#f1f1f6] px-3 py-2.5 text-left text-[13px] text-foreground/85 transition-colors last:border-b-0 hover:bg-accent/70"
                >
                  {dir.is_git ? (
                    <FolderGit2 className="size-4 shrink-0 text-success" />
                  ) : (
                    <Folder className="size-4 shrink-0 text-[#a3a5b5]" />
                  )}
                  <span className="min-w-0 flex-1 truncate">{dir.name}</span>
                  {dir.is_git ? <Badge variant="success">git</Badge> : null}
                  <ChevronRight className="size-3.5 shrink-0 text-faint" />
                </button>
              ))
            ) : (
              <EmptyState compact icon={Search} title="此文件夹下没有子文件夹" />
            )}
          </div>
        </DialogBody>

        <DialogFooter>
          <Button
            variant="ghost"
            className="mr-auto"
            disabled={state.at_root}
            onClick={() => void browse(state.parent ?? '')}
          >
            <ArrowUp />
            上一级
          </Button>
          <Button variant="outline" onClick={() => void browse('')}>
            <Home />
            主目录
          </Button>
          <Button
            disabled={!state.current}
            onClick={() => {
              onPick(state.current);
              onOpenChange(false);
            }}
          >
            <Check />
            选择当前文件夹
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}