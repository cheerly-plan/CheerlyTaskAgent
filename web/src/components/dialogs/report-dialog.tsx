import {Download} from 'lucide-react';

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

interface ReportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** renderMarkdown() 的输出 */
  html: string;
  downloadUrl: string | null;
  downloadName: string;
}

export function ReportDialog({
  open,
  onOpenChange,
  title,
  html,
  downloadUrl,
  downloadName,
}: ReportDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[800px]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="sr-only">工单详情与执行报告</DialogDescription>
        </DialogHeader>

        <DialogBody>
          {html ? (
            <div className="report-body" dangerouslySetInnerHTML={{__html: html}} />
          ) : (
            <p className="py-8 text-center text-xs text-faint">正在载入报告…</p>
          )}
        </DialogBody>

        <DialogFooter>
          {downloadUrl ? (
            <Button variant="outline" className="mr-auto" asChild>
              <a href={downloadUrl} download={downloadName}>
                <Download />
                下载 .md
              </a>
            </Button>
          ) : null}
          <Button onClick={() => onOpenChange(false)}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}