import * as React from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  CircleCheck,
  CircleSlash,
  Download,
  FileDiff,
  Files,
  FolderGit2,
  FolderTree,
  ListChecks,
  Loader2,
  Timer,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';

import {useApp} from '@/app-context';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {renderMarkdown} from '@/lib/markdown';
import {kindLabel, statusLabel} from '@/lib/labels';
import type {Attention, Fact, ReportSummary, Tone} from '@/lib/report-view';
import {cn} from '@/lib/utils';

const TONE_TEXT: Record<Tone, string> = {
  success: 'text-success-foreground',
  warning: 'text-warning-foreground',
  danger: 'text-destructive',
  info: 'text-info-foreground',
  neutral: 'text-foreground',
};

const TONE_ICON_BG: Record<Tone, string> = {
  success: 'bg-success-muted text-success-foreground',
  warning: 'bg-warning-muted text-warning-foreground',
  danger: 'bg-destructive-muted text-destructive',
  info: 'bg-info-muted text-info-foreground',
  neutral: 'bg-secondary text-muted-foreground',
};

const TONE_BAR: Record<Tone, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-destructive',
  info: 'bg-info',
  neutral: 'bg-[#c9cbd8]',
};

const ATTENTION_ICON: Record<Attention['kind'], LucideIcon> = {
  error: AlertTriangle,
  failure: AlertTriangle,
  conflict: TriangleAlert,
  warning: TriangleAlert,
  files: FileDiff,
  info: ListChecks,
};

const ROW_BORDER: Record<Tone, string> = {
  success: 'border-l-success',
  warning: 'border-l-warning',
  danger: 'border-l-destructive',
  info: 'border-l-info',
  neutral: 'border-l-border',
};

/**
 * 报告详情页：整页展示一次执行的完整结果。
 *
 * 版面（宽屏两栏）：结论横幅横贯顶部 → 左栏「概览统计 / 需求描述 / 改动明细」，
 * 右栏「核心信息 / 同步情况 / 原始输出」。
 *
 * 与旧弹窗的差别：不受 880px 宽度和视口高度限制，信息一屏铺开；告一段落后
 * 用「← 返回」回到来源列表；请求还没回来时先给出加载中的骨架，而不是空白。
 */
export function ReportPage() {
  const {reportView, closeReport} = useApp();
  const {loading, error, summary, markdown, item, downloadUrl, downloadName, backLabel} = reportView;

  // 正文 = 报告去掉标题与「基本信息 / 执行结果」两张主表后的部分（需求描述、
  // 改动原因、验证结果、折叠的原始输出）。这些表已经在页面上方结构化展示，
  // 再渲染一遍会和「核心信息 / 指标卡」重复。注意必须整段渲染：
  // 折叠块里的 `##` 级标题要跟它的 <details> 一起输出，不能按小节切碎。
  const reportHtml = React.useMemo(
    () => renderMarkdown(summary?.rest?.trim() ? summary.rest : (markdown ?? '')),
    [summary, markdown],
  );

  return (
    <div className="mx-auto w-full max-w-[1180px]">
      {/* 页头：返回 + 标题 + 下载 */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <Button variant="ghost" size="icon-sm" onClick={closeReport} title={`返回${backLabel}`} aria-label={`返回${backLabel}`}>
            <ArrowLeft />
          </Button>
          <div className="min-w-0">
            <button
              type="button"
              onClick={closeReport}
              className="mb-1 flex items-center gap-1 text-[11.5px] text-faint hover:text-foreground"
            >
              返回{backLabel}
            </button>
            <h1 className="truncate text-[17px] font-semibold leading-tight tracking-[-0.01em] text-foreground">
              {summary?.subject || item?.title || '执行报告'}
            </h1>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              {item ? <Badge variant="outline">{kindLabel[item.kind] ?? item.kind}</Badge> : null}
              {item ? <Badge variant="purple">{statusLabel[item.status] ?? item.status}</Badge> : null}
              <span className="font-mono text-[11px] text-faint">{item?.id}</span>
            </div>
          </div>
        </div>
        {downloadUrl ? (
          <Button variant="outline" size="sm" asChild>
            <a href={downloadUrl} download={downloadName}>
              <Download />
              下载 .md
            </a>
          </Button>
        ) : null}
      </div>

      {loading ? (
        <ReportSkeleton />
      ) : error ? (
        <Card className="flex items-center gap-2 border-destructive/30 bg-destructive-muted/50 px-4 py-3 text-xs text-destructive">
          <AlertTriangle className="size-4" />
          打开报告失败：{error}
        </Card>
      ) : summary ? (
        <ReportView summary={summary} reportHtml={reportHtml} />
      ) : (
        // 没有报告（如 dry-run 未执行）：展示工单当前信息 + 生成条件说明。
        <Card className="p-4">
          <div className="report-body" dangerouslySetInnerHTML={{__html: reportHtml}} />
        </Card>
      )}
    </div>
  );
}

function ReportView({summary, reportHtml}: {summary: ReportSummary; reportHtml: string}) {
  const jump = (ref: string) => {
    document.getElementById(ref)?.scrollIntoView({behavior: 'smooth', block: 'start'});
  };

  return (
    <div className="space-y-3">
      <ReportOutcomeBanner summary={summary} onJump={jump} />
      <ReportStats stats={summary.stats} />

      <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-3">
          <Card id="report-attention" className="scroll-mt-4 p-4">
            <CardTitle icon={ListChecks}>需要关注</CardTitle>
            <ReportAttention attention={summary.attention} onJump={jump} />
          </Card>

          <Card id="report-full" className="scroll-mt-4 p-4">
            <CardTitle icon={FileDiff}>需求与报告正文</CardTitle>
            {/* 需求描述、改动原因与验证结论；执行命令 / 提示词 / 原始输出折叠在文末。 */}
            <div className="report-body" dangerouslySetInnerHTML={{__html: reportHtml}} />
          </Card>

          <Card id="report-changes" className="scroll-mt-4 p-4">
            <CardTitle
              icon={FolderTree}
              extra={<span className="text-[11.5px] text-faint">共 {summary.changedFiles.length} 个文件</span>}
            >
              改动明细
            </CardTitle>
            <ReportChanges summary={summary} />
          </Card>

        </div>

        <div className="min-w-0 space-y-3">
          <Card className="p-4">
            <CardTitle icon={FolderGit2}>核心信息</CardTitle>
            <ReportMeta summary={summary} />
          </Card>

          {summary.sync ? (
            <Card className="p-4">
              <CardTitle icon={Files}>同步到主仓库</CardTitle>
              <ReportSync summary={summary} />
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** 结论横幅：颜色 + 图标 + 一句话结论，替代埋在表格里的「成功 | 是」。 */
function ReportOutcomeBanner({summary, onJump}: {summary: ReportSummary; onJump: (ref: string) => void}) {
  const tone: Tone = summary.outcome === 'success' ? 'success' : summary.outcome === 'failure' ? 'danger' : 'neutral';
  const Icon = tone === 'success' ? CircleCheck : tone === 'danger' ? AlertTriangle : CircleSlash;
  const conflicts = summary.conflicted.length;

  return (
    <div
      className={cn(
        'flex items-start gap-3 rounded-xl border p-4',
        tone === 'success' && 'border-success/25 bg-success-muted/60',
        tone === 'danger' && 'border-destructive/25 bg-destructive-muted/60',
        tone === 'neutral' && 'border-border bg-secondary/60',
      )}
    >
      <span className={cn('grid size-9 shrink-0 place-items-center rounded-lg', TONE_ICON_BG[tone])}>
        <Icon className="size-[18px]" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className={cn('text-sm font-semibold', TONE_TEXT[tone])}>
            {summary.outcome === 'success' ? '执行成功' : summary.outcome === 'failure' ? '执行未通过' : '尚未执行'}
          </span>
          {summary.meta.mode ? (
            <Badge variant={summary.dryRun ? 'info' : 'purple'}>{summary.dryRun ? 'dry-run 试运行' : 'apply 已落地'}</Badge>
          ) : null}
          {summary.diagnostics.map((label) => (
            <Badge key={label} variant="warning">
              {label}
            </Badge>
          ))}
        </div>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{summary.verdict}</p>
        {conflicts || summary.sync?.error ? (
          <button
            type="button"
            onClick={() => onJump('report-attention')}
            className={cn(
              'mt-1.5 text-xs font-medium underline-offset-2 hover:underline',
              conflicts ? 'text-[#a4610a]' : 'text-destructive',
            )}
          >
            {conflicts ? `${conflicts} 个文件同步冲突，查看处理建议 →` : '同步到主仓库出错，查看详情 →'}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** 四张指标卡：与概览页 KPI 卡同一套视觉。 */
function ReportStats({stats}: {stats: ReportSummary['stats']}) {
  const icons: LucideIcon[] = [CircleCheck, Timer, Files, FolderTree];
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {stats.map((stat, index) => {
        const Icon = icons[index] ?? CircleCheck;
        return (
          <div key={stat.label} className="rounded-xl border border-border bg-card px-3.5 py-3 shadow-sm">
            <div className="flex items-center gap-1.5">
              <span className={cn('grid size-5 shrink-0 place-items-center rounded-md', TONE_ICON_BG[stat.tone])}>
                <Icon className="size-3" />
              </span>
              <span className="truncate text-[11.5px] text-faint">{stat.label}</span>
            </div>
            <div className={cn('mt-1 truncate text-[19px] font-semibold leading-tight tracking-[-0.02em]', TONE_TEXT[stat.tone])}>
              {stat.value}
            </div>
            {stat.hint ? <div className="truncate text-[11px] text-faint">{stat.hint}</div> : null}
          </div>
        );
      })}
    </div>
  );
}

/** 需要关注：错误 / 冲突 / 截断 / 未落库，按严重程度前置。 */
function ReportAttention({attention, onJump}: {attention: Attention[]; onJump: (ref: string) => void}) {
  if (!attention.length) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-faint">
        <CircleCheck className="size-3.5" />
        没有需要特别处理的事项。
      </p>
    );
  }
  return (
    <ul className="space-y-1.5">
      {attention.map((entry) => {
        const Icon = ATTENTION_ICON[entry.kind];
        const target = entry.kind === 'files' ? 'report-changes' : entry.kind === 'warning' ? 'report-full' : null;
        return (
          <li
            key={`${entry.kind}-${entry.title}`}
            className={cn('rounded-lg border border-border/70 border-l-[3px] bg-card px-3 py-2', ROW_BORDER[entry.tone])}
          >
            <div className="flex items-start gap-2">
              <Icon className={cn('mt-[3px] size-3.5 shrink-0', TONE_TEXT[entry.tone])} />
              <div className="min-w-0 flex-1">
                <div className={cn('text-xs font-medium', TONE_TEXT[entry.tone])}>{entry.title}</div>
                {entry.detail ? (
                  <p className="mt-0.5 break-words text-[11.5px] leading-relaxed text-muted-foreground">{entry.detail}</p>
                ) : null}
                {entry.items?.length ? (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {entry.items.slice(0, 8).map((file) => (
                      <code key={file} className="rounded bg-secondary px-1.5 py-px font-mono text-[10.5px] text-foreground/75">
                        {file}
                      </code>
                    ))}
                    {entry.more ? <span className="text-[10.5px] text-faint">等 {entry.more} 个</span> : null}
                  </div>
                ) : null}
                {target ? (
                  <button
                    type="button"
                    onClick={() => onJump(target)}
                    className="mt-1 text-[11px] font-medium text-primary underline-offset-2 hover:underline"
                  >
                    查看详情 →
                  </button>
                ) : null}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** 核心信息：来源 / 类型 / 模型 / 仓库等，纵向排列适配右栏。 */
function ReportMeta({summary}: {summary: ReportSummary}) {
  const basic = summary.groups.find((group) => group.label === '基本信息') ?? summary.groups[0];
  const execution = summary.groups.find((group) => group.label !== '基本信息');
  if (!basic?.facts.length) return null;

  const factRow = (fact: Fact) => (
    <div key={fact.label} className="flex min-w-0 items-baseline gap-2 py-[3px] text-xs">
      <dt className="w-[64px] shrink-0 text-faint">{fact.label}</dt>
      <dd
        className={cn(
          'min-w-0 flex-1 break-all text-foreground/85',
          (fact.label === '状态' || fact.label === '类型') && 'font-medium',
          fact.mono && 'font-mono text-[11px]',
        )}
        title={fact.value}
      >
        {displayValue(fact.label, fact.value)}
      </dd>
    </div>
  );

  return (
    <div className="space-y-2.5">
      <dl className="grid grid-cols-1">{basic.facts.map(factRow)}</dl>
      {execution ? (
        <div>
          <div className="mb-1 border-t border-border pt-2 text-[11px] font-semibold uppercase tracking-wide text-faint">
            {execution.label}
          </div>
          <dl className="grid grid-cols-1">{execution.facts.map(factRow)}</dl>
        </div>
      ) : null}
    </div>
  );
}

/** 同步明细：数量 + 备份目录 + 删除/跳过清单。 */
function ReportSync({summary}: {summary: ReportSummary}) {
  const {sync} = summary;
  if (!sync) return null;
  const rows = [
    {label: '已同步', value: summary.syncedCount, tone: 'success' as Tone},
    {label: '冲突跳过', value: summary.conflicted.length, tone: summary.conflicted.length ? ('warning' as Tone) : ('neutral' as Tone)},
    {label: '已删除', value: sync.deleted.length, tone: 'neutral' as Tone},
    {label: '已跳过', value: sync.skipped.length, tone: 'neutral' as Tone},
  ];
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        {rows.map((row) => (
          <div key={row.label} className="rounded-lg bg-secondary/60 px-2.5 py-1.5">
            <div className={cn('text-base font-semibold leading-tight', TONE_TEXT[row.tone])}>{row.value}</div>
            <div className="text-[11px] text-faint">{row.label}</div>
          </div>
        ))}
      </div>
      {sync.error ? (
        <p className="rounded-md bg-destructive-muted px-2.5 py-1.5 text-[11.5px] text-destructive">同步错误：{sync.error}</p>
      ) : null}
      {sync.backupDir ? (
        <div className="text-[11px] text-faint">
          备份目录
          <div className="mt-0.5 break-all font-mono text-[10.5px] text-foreground/70">{sync.backupDir}</div>
        </div>
      ) : null}
    </div>
  );
}

/** 改动明细：按目录聚合的条形图 + 文件清单。 */
function ReportChanges({summary}: {summary: ReportSummary}) {
  const {modules, changedFiles} = summary;
  if (!changedFiles.length) {
    return <p className="text-xs text-faint">本次执行没有改动任何文件。</p>;
  }
  const tone: Tone = summary.dryRun ? 'info' : 'success';
  return (
    <div className="space-y-3">
      <div className="grid gap-x-6 gap-y-2 md:grid-cols-2">
        {modules.map((slice) => (
          <div key={slice.name} title={slice.files.join('\n')}>
            <div className="mb-1 flex items-baseline justify-between gap-2 text-[11.5px]">
              <span className="truncate font-mono text-foreground/80">{slice.name}</span>
              <span className="shrink-0 text-faint">{slice.count} 个</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
              <div className={cn('h-full rounded-full', TONE_BAR[tone])} style={{width: `${Math.max(6, slice.percent)}%`}} />
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-1">
        {changedFiles.map((file) => (
          <code
            key={file}
            title={file}
            className="max-w-full truncate rounded-md border border-border bg-[#fafafd] px-1.5 py-0.5 font-mono text-[11px] text-foreground/75"
          >
            {file}
          </code>
        ))}
      </div>
    </div>
  );
}

function CardTitle({
  icon: Icon,
  children,
  extra,
}: {
  icon: LucideIcon;
  children: React.ReactNode;
  extra?: React.ReactNode;
}) {
  return (
    <div className="mb-2.5 flex items-center justify-between gap-2">
      <div className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
        <Icon className="size-3.5 text-faint" />
        {children}
      </div>
      {extra}
    </div>
  );
}

function ReportSkeleton() {
  return (
    <div className="space-y-3">
      <div className="h-[74px] animate-pulse rounded-xl border border-border bg-secondary/50" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="h-[74px] animate-pulse rounded-xl border border-border bg-secondary/40" />
        ))}
      </div>
      <Card className="flex items-center justify-center gap-2 py-10 text-xs text-faint">
        <Loader2 className="size-4 animate-spin" />
        正在载入报告…
      </Card>
    </div>
  );
}

/** 状态 / 类型用中文标签，其余原样展示。 */
function displayValue(label: string, value: string): string {
  if (label === '状态') return statusLabel[value] ?? value;
  if (label === '类型') return kindLabel[value] ?? value;
  return value;
}