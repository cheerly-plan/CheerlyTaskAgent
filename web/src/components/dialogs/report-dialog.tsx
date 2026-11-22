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
import {Tabs, TabsContent, TabsList, TabsTrigger} from '@/components/ui/tabs';
import type {ReportSection} from '@/lib/markdown';

interface ReportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** renderReportSections() 的输出，每个板块对应一个 tab */
  sections: ReportSection[];
  downloadUrl: string | null;
  downloadName: string;
}

export function ReportDialog({
  open,
  onOpenChange,
  title,
  sections,
  downloadUrl,
  downloadName,
}: ReportDialogProps) {
  const tabs = sections.map((section, index) => ({...section, value: `section-${index}`}));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[800px]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="sr-only">工单详情与执行报告</DialogDescription>
        </DialogHeader>

        <DialogBody className="flex flex-col !overflow-hidden">
          {tabs.length ? (
            <Tabs key={title} defaultValue={tabs[0].value} className="flex min-h-0 flex-1 flex-col">
              <TabsList className="mb-2 flex h-auto shrink-0 flex-wrap justify-start gap-1 rounded-md bg-transparent p-0">
                {tabs.map((tab) => (
                  <TabsTrigger
                    key={tab.value}
                    value={tab.value}
                    className="rounded-md border border-transparent px-3 py-1.5 text-xs data-[state=active]:border-border data-[state=active]:bg-secondary data-[state=active]:text-foreground data-[state=active]:shadow-none"
                  >
                    {tab.label}
                  </TabsTrigger>
                ))}
              </TabsList>
              {tabs.map((tab) => (
                <TabsContent
                  key={tab.value}
                  value={tab.value}
                  className="scrollbar-thin mt-0 min-h-0 flex-1 overflow-y-auto pr-1"
                >
                  <div className="report-body" dangerouslySetInnerHTML={{__html: tab.html}} />
                </TabsContent>
              ))}
            </Tabs>
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
