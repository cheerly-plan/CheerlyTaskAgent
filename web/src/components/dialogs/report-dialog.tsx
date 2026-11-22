import * as React from 'react';
import {Download} from 'lucide-react';

import {ReportAttention, ReportChanges, ReportMeta, ReportOutcomeBanner, ReportStats} from '@/components/report-summary';
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
import {analyzeReport, type ReportSummary} from '@/lib/report-view';
import {renderMarkdown} from '@/lib/markdown';

interface ReportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** 报告原文 Markdown（没有报告时为 null，走「工单信息」降级视图）。 */
  markdown: string | null;
  /** 没有报告时的降级摘要（工单的当前字段）。 */
  fallbackMarkdown?: string | null;
  downloadUrl: string | null;
  downloadName: string;
}

type TabValue = 'summary' | 'attention' | 'changes' | 'full';

/**
 * 报告弹窗：先给结论，再给证据。
 *
 * 1. 顶部常驻「一眼看清」区：结论横幅 → 四张指标卡 → 需要注意 / 核心信息，
 *    不随标签页滚动，打开即可判断这次执行成没成、动了哪些文件、有没有要处理的冲突。
 * 2. 下方标签页放细节：概览（分组字段）、需要注意（风险清单）、改动（模块分布 + 文件）、
 *    完整报告（原始 Markdown 渲染，含折叠的原始输出）。
 *
 * 原来的实现直接把三张表格平铺在同一个滚动区里，「成功 | 是」「耗时 517.00s」
 * 这类关键信息要滚动才能看到，长报告里改动文件和原始输出还会把结论挤到视野之外。
 */
export function ReportDialog({
  open,
  onOpenChange,
  title,
  markdown,
  fallbackMarkdown,
  downloadUrl,
  downloadName,
}: ReportDialogProps) {
  const summary = React.useMemo<ReportSummary | null>(
    () => (markdown ? analyzeReport(markdown) : null),
    [markdown],
  );
  const [tab, setTab] = React.useState<TabValue>('summary');

  // 每次打开新报告都回到「概览」，避免沿用上一篇的标签页。
  React.useEffect(() => {
    if (open) setTab('summary');
  }, [open, title, markdown]);

  const jump = (next: TabValue) => setTab(next);
  const attentionCount = summary?.attention.filter((entry) => entry.tone === 'danger' || entry.tone === 'warning').length ?? 0;

  const fullHtml = React.useMemo(() => {
    if (summary) {
      return {
        overview: renderMarkdown(summary.rest),
        full: renderMarkdown(markdown ?? ''),
      };
    }
    return {overview: renderMarkdown(fallbackMarkdown ?? ''), full: ''};
  }, [summary, markdown, fallbackMarkdown]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[880px]">
        <DialogHeader>
          <DialogTitle>{summary ? `执行报告 · ${summary.subject || title}` : title}</DialogTitle>
          <DialogDescription className="sr-only">工单详情与执行报告</DialogDescription>
        </DialogHeader>

        {summary ? (
          <DialogBody className="scrollbar-thin space-y-2 py-3">
            <ReportOutcomeBanner summary={summary} onJumpTo={(next) => jump(next)} />
            <ReportStats stats={summary.stats} />

            <div className="grid gap-2.5 md:grid-cols-2">
              <div className="space-y-1.5">
                <SectionLabel>需要注意</SectionLabel>
                <ReportAttention attention={summary.attention} onJumpTo={(next) => jump(next)} />
              </div>
              <div className="space-y-1.5">
                <SectionLabel>改动概览</SectionLabel>
                <ReportChanges summary={summary} />
              </div>
            </div>

            <ReportMeta summary={summary} onJumpTo={(next) => jump(next)} />

            <Tabs value={tab} onValueChange={(value) => setTab(value as TabValue)} className="pt-0.5">
              <TabsList className="flex h-auto flex-wrap justify-start gap-1 rounded-md bg-transparent p-0">
                <TabTrigger value="summary">概览</TabTrigger>
                <TabTrigger value="attention">
                  需要注意
                  {attentionCount ? (
                    <span className="ml-1 rounded-full bg-warning-muted px-1.5 text-[10.5px] font-semibold text-warning-foreground">
                      {attentionCount}
                    </span>
                  ) : null}
                </TabTrigger>
                <TabTrigger value="changes">改动 {summary.changedFiles.length || ''}</TabTrigger>
                <TabTrigger value="full">完整报告</TabTrigger>
              </TabsList>

              <TabsContent value="summary" className="scrollbar-thin mt-2 max-h-[28vh] overflow-y-auto pr-1">
                <div className="report-body" dangerouslySetInnerHTML={{__html: fullHtml.overview}} />
              </TabsContent>
              <TabsContent value="attention" className="mt-2 max-h-[28vh] overflow-y-auto pr-1">
                <ReportAttention attention={summary.attention} onJumpTo={(next) => jump(next)} />
              </TabsContent>
              <TabsContent value="changes" className="mt-2 max-h-[28vh] overflow-y-auto pr-1">
                <div className="space-y-3">
                  <ReportChanges summary={summary} />
                  {summary.sync ? (
                    <div className="rounded-lg border border-border bg-[#fcfcfe] px-3 py-2 text-[11.5px] text-muted-foreground">
                      <div className="mb-1 font-semibold text-foreground">同步到主仓库</div>
                      <div>
                        已同步 {summary.syncedCount} 个
                        {summary.sync.deleted.length ? ` · 已删除 ${summary.sync.deleted.length} 个` : ''}
                        {summary.sync.skipped.length ? ` · 跳过 ${summary.sync.skipped.length} 个` : ''}
                      </div>
                      {summary.sync.backupDir ? (
                        <div className="mt-0.5 truncate font-mono text-[10.5px]" title={summary.sync.backupDir}>
                          备份目录：{summary.sync.backupDir}
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </TabsContent>
              <TabsContent value="full" className="scrollbar-thin mt-2 max-h-[28vh] overflow-y-auto pr-1">
                <div className="report-body" dangerouslySetInnerHTML={{__html: fullHtml.full}} />
              </TabsContent>
            </Tabs>
          </DialogBody>
        ) : (
          <DialogBody className="scrollbar-thin py-4">
            <div className="report-body" dangerouslySetInnerHTML={{__html: fullHtml.overview}} />
          </DialogBody>
        )}

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

function SectionLabel({children}: {children: React.ReactNode}) {
  return <div className="text-[11px] font-semibold uppercase tracking-wide text-faint">{children}</div>;
}

function TabTrigger({value, children}: {value: TabValue; children: React.ReactNode}) {
  return (
    <TabsTrigger
      value={value}
      className="rounded-md border border-transparent px-3 py-1.5 text-xs data-[state=active]:border-border data-[state=active]:bg-secondary data-[state=active]:text-foreground data-[state=active]:shadow-none"
    >
      {children}
    </TabsTrigger>
  );
}