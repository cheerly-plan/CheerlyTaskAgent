import * as React from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  CircleCheck,
  CircleSlash,
  ChevronRight,
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
import {Tooltip, TooltipContent, TooltipTrigger} from '@/components/ui/tooltip';
import {renderMarkdown} from '@/lib/markdown';
import {kindLabel, statusLabel} from '@/lib/labels';
import type {Attention, ReportSummary, Tone} from '@/lib/report-view';
import {cn} from '@/lib/utils';

/**
 * 报告详情页。
 *
 * 版面分两层：
 * 1. 结论层（通栏）：页头 → 结论横幅 → 关键数字 → 需要关注。
 *    打开即知成败、耗时与风险，不需要滚动。
 * 2. 证据层（两栏）：左侧是长文阅读区（需求与报告正文、改动明细），
 *    右侧是随时可查的「工单信息」。
 *
 * 排版原则：长文用 13.5px / 1.9 行高，卡片内边距 5、卡片间距 5；
 * 元信息用键值对齐而不是密集的小字行，避免「一眼看上去全是字」。
 */

const LAYOUT_WIDTH = 'mx-auto w-full max-w-[1240px]';

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
  success: 'bg-gradient-to-r from-success/45 to-success',
  warning: 'bg-warning',
  danger: 'bg-destructive',
  info: 'bg-gradient-to-r from-info/45 to-info',
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

/** 提示条左侧色带：风险等级一眼可辨。 */
const ALERT_ACCENT: Record<Tone, string> = {
  success: 'border-l-success',
  warning: 'border-l-warning',
  danger: 'border-l-destructive',
  info: 'border-l-info',
  neutral: 'border-l-border',
};

const ALERT_BG: Record<Tone, string> = {
  success: 'bg-success-muted/50',
  warning: 'bg-warning-muted/60',
  danger: 'bg-destructive-muted/60',
  info: 'bg-info-muted/40',
  neutral: 'bg-secondary/50',
};

function IconTip({label, children}: {label: string; children: React.ReactNode}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="top" className="bg-[#1f1f28] px-2.5 py-1 text-[11px] text-white">
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

export function ReportPage() {
  const {reportView, closeReport} = useApp();
  const {loading, error, summary, markdown, item, downloadUrl, downloadName, backLabel} = reportView;

  // 正文从「需求描述」开始，标题与前两张主表已结构化展示在别处；
  // 必须整段渲染，折叠块里的标题要跟它的 <details> 一起输出。
  const reportHtml = React.useMemo(
    () => renderMarkdown(summary?.rest?.trim() ? summary.rest : (markdown ?? '')),
    [summary, markdown],
  );

  return (
    <div className={LAYOUT_WIDTH}>
      <PageHeader
        title={summary?.subject || item?.title || '执行报告'}
        id={item?.id}
        kind={item ? (kindLabel[item.kind] ?? item.kind) : undefined}
        backLabel={backLabel}
        onBack={closeReport}
        downloadUrl={downloadUrl}
        downloadName={downloadName}
      />

      {loading ? (
        <ReportSkeleton />
      ) : error ? (
        <div className="flex items-center gap-2.5 rounded-2xl border border-destructive/25 bg-destructive-muted/60 px-5 py-4 text-[13px] text-destructive">
          <AlertTriangle className="size-4 shrink-0" />
          打开报告失败：{error}
        </div>
      ) : summary ? (
        <ReportView summary={summary} reportHtml={reportHtml} />
      ) : (
        <Panel>
          <div className="report-body" dangerouslySetInnerHTML={{__html: reportHtml}} />
        </Panel>
      )}
    </div>
  );
}

/** 页头：返回 + 标题 + 类型/编号 + 下载。 */
function PageHeader({
  title,
  id,
  kind,
  backLabel,
  onBack,
  downloadUrl,
  downloadName,
}: {
  title: string;
  id?: string;
  kind?: string;
  backLabel: string;
  onBack: () => void;
  downloadUrl: string | null;
  downloadName: string;
}) {
  return (
    <header className="mb-6 flex items-start justify-between gap-4">
      <div className="flex min-w-0 items-start gap-3.5">
        <IconTip label={`返回${backLabel}`}>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={onBack}
            aria-label={`返回${backLabel}`}
            className="mt-0.5 shrink-0 bg-card"
          >
            <ArrowLeft />
          </Button>
        </IconTip>
        <div className="min-w-0">
          <h1 className="truncate text-[21px] font-semibold leading-[1.3] tracking-[-0.02em] text-foreground">
            {title}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-faint">
            {kind ? <Badge variant="outline" className="bg-card">{kind}</Badge> : null}
            {id ? <span className="font-mono text-[11.5px]">{id}</span> : null}
            <span className="text-faint/70">·</span>
            <button type="button" onClick={onBack} className="hover:text-foreground hover:underline">
              {backLabel}
            </button>
          </div>
        </div>
      </div>
      {downloadUrl ? (
        <Button variant="outline" size="sm" className="shrink-0 bg-card" asChild>
          <a href={downloadUrl} download={downloadName}>
            <Download />
            下载 .md
          </a>
        </Button>
      ) : null}
    </header>
  );
}

function ReportView({summary, reportHtml}: {summary: ReportSummary; reportHtml: string}) {
  const jump = (ref: string) => {
    document.getElementById(ref)?.scrollIntoView({behavior: 'smooth', block: 'start'});
  };

  return (
    <div className="space-y-5">
      {/* 结论层 */}
      <ReportOutcomeBanner summary={summary} onJump={jump} />
      <ReportStats stats={summary.stats} />
      <ReportAttentionList attention={summary.attention} />

      {/* 证据层 */}
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_310px]">
        <div className="min-w-0 space-y-5">
          <Panel>
            <PanelHeading icon={FileDiff}>需求与报告正文</PanelHeading>
            <div className="report-body" dangerouslySetInnerHTML={{__html: reportHtml}} />
          </Panel>

          <Panel id="report-changes" className="scroll-mt-6">
            <PanelHeading
              icon={FolderTree}
              extra={<span className="text-[11.5px] text-faint">共 {summary.changedFiles.length} 个文件</span>}
            >
              改动明细
            </PanelHeading>
            <ReportChanges summary={summary} />
          </Panel>
        </div>

        <aside className="space-y-5 xl:sticky xl:top-0">
          <Panel>
            <PanelHeading icon={FolderGit2}>工单信息</PanelHeading>
            <ReportMeta summary={summary} />
          </Panel>

          {summary.sync ? (
            <Panel>
              <PanelHeading icon={Files}>同步到主仓库</PanelHeading>
              <ReportSync summary={summary} />
            </Panel>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

/** 结论横幅：成败 + 一句话结论 + 试运行/警示标签。 */
function ReportOutcomeBanner({summary, onJump}: {summary: ReportSummary; onJump: (ref: string) => void}) {
  const tone: Tone = summary.outcome === 'success' ? 'success' : summary.outcome === 'failure' ? 'danger' : 'neutral';
  const Icon = tone === 'success' ? CircleCheck : tone === 'danger' ? AlertTriangle : CircleSlash;
  const conflicts = summary.conflicted.length;

  return (
    <div
      className={cn(
        'flex items-center gap-4 rounded-2xl border px-5 py-4',
        tone === 'success' && 'border-success/20 bg-gradient-to-r from-success-muted/80 to-success-muted/30',
        tone === 'danger' && 'border-destructive/20 bg-gradient-to-r from-destructive-muted/80 to-destructive-muted/30',
        tone === 'neutral' && 'border-border bg-secondary/50',
      )}
    >
      <span className={cn('grid size-11 shrink-0 place-items-center rounded-xl', TONE_ICON_BG[tone])}>
        <Icon className="size-5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <span className={cn('text-[15px] font-semibold', TONE_TEXT[tone])}>
            {summary.outcome === 'success' ? '执行成功' : summary.outcome === 'failure' ? '执行未通过' : '尚未执行'}
          </span>
          {summary.meta.mode ? (
            <Badge
              variant={summary.dryRun ? 'info' : 'purple'}
              className="bg-white/70 backdrop-blur-sm"
            >
              {summary.dryRun ? 'dry-run 试运行' : 'apply 已落地'}
            </Badge>
          ) : null}
          {summary.diagnostics.map((label) => (
            <Badge key={label} variant="warning" className="bg-white/70 backdrop-blur-sm">
              {label}
            </Badge>
          ))}
        </div>
        <p className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">{summary.verdict}</p>
      </div>
      {conflicts || summary.sync?.error ? (
        <button
          type="button"
          onClick={() => onJump('report-attention')}
          className={cn(
            'flex shrink-0 items-center gap-0.5 rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors',
            conflicts
              ? 'bg-warning-muted text-warning-foreground hover:bg-warning-muted/70'
              : 'bg-destructive-muted text-destructive hover:bg-destructive-muted/70',
          )}
        >
          {conflicts ? `${conflicts} 个文件冲突` : '同步出错'}
          <ChevronRight className="size-3.5" />
        </button>
      ) : null}
    </div>
  );
}

/**
 * 关键数字：一条横向数据带。
 * 四张独立卡片在视觉上过于抢眼（大片留白 + 厚重描边），合并成一组更收敛。
 */
function ReportStats({stats}: {stats: ReportSummary['stats']}) {
  const icons: LucideIcon[] = [CircleCheck, Timer, Files, FolderTree];
  return (
    <div className="grid grid-cols-2 divide-border overflow-hidden rounded-2xl border border-border bg-card shadow-sm sm:grid-cols-4 sm:divide-x">
      {stats.map((stat, index) => {
        const Icon = icons[index] ?? CircleCheck;
        return (
          <div
            key={stat.label}
            className={cn(
              'px-5 py-4',
              index >= 2 && 'border-t border-border sm:border-t-0',
              index % 2 === 1 && 'border-l border-border sm:border-l-0',
            )}
          >
            <div className="flex items-center gap-1.5 text-[11.5px] text-faint">
              <Icon className={cn('size-3.5', TONE_TEXT[stat.tone])} />
              {stat.label}
            </div>
            <div
              className={cn(
                'mt-1.5 truncate text-[23px] font-semibold leading-none tracking-[-0.03em]',
                TONE_TEXT[stat.tone],
              )}
            >
              {stat.value}
            </div>
            {stat.hint ? <div className="mt-1.5 truncate text-[11px] text-faint">{stat.hint}</div> : null}
          </div>
        );
      })}
    </div>
  );
}

/** 需要关注：全宽分段。正常时收成一行，不占版面。 */
function ReportAttentionList({attention}: {attention: Attention[]}) {
  if (!attention.length) {
    return (
      <p className="flex items-center gap-1.5 px-1 text-[11.5px] text-faint">
        <CircleCheck className="size-3.5 text-success/80" />
        没有需要特别处理的事项
      </p>
    );
  }

  return (
    <section id="report-attention" className="scroll-mt-6">
      <div className="mb-2 flex items-center gap-1.5 px-0.5 text-[12px] font-semibold text-foreground">
        <ListChecks className="size-3.5 text-faint" />
        需要关注
      </div>
      <ul className="grid gap-2.5 md:grid-cols-2">
        {attention.map((entry) => {
          const Icon = ATTENTION_ICON[entry.kind];
          return (
            <li
              key={`${entry.kind}-${entry.title}`}
              className={cn(
                'rounded-xl border border-border/70 border-l-[3px] px-4 py-3',
                ALERT_ACCENT[entry.tone],
                ALERT_BG[entry.tone],
              )}
            >
              <div className="flex items-start gap-2.5">
                <Icon className={cn('mt-0.5 size-4 shrink-0', TONE_TEXT[entry.tone])} />
                <div className="min-w-0 flex-1">
                  <div className={cn('text-[12.5px] font-semibold', TONE_TEXT[entry.tone])}>{entry.title}</div>
                  {entry.detail ? (
                    <p className="mt-1 break-words text-[11.5px] leading-relaxed text-muted-foreground">{entry.detail}</p>
                  ) : null}
                  {entry.items?.length ? (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {entry.items.slice(0, 6).map((file) => (
                        <code
                          key={file}
                          className="rounded-md bg-white/80 px-1.5 py-0.5 font-mono text-[10.5px] text-foreground/75"
                        >
                          {file}
                        </code>
                      ))}
                      {entry.more ? <span className="self-center text-[10.5px] text-faint">等 {entry.more} 个</span> : null}
                    </div>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** 工单信息：键值左对齐，长路径等宽换行；执行结果只保留非重复项。 */
function ReportMeta({summary}: {summary: ReportSummary}) {
  const {meta} = summary;
  // 执行结果表里的成功 / dry-run / 耗时已由横幅与数字带展示，这里只保留 worktree。
  const worktree = meta.worktree && meta.worktree !== '—' ? meta.worktree : '';

  // 来源是「manual 手动单号」「tapd tapd:story:…」这种两段式，拆开显示更易读。
  const [sourceName, sourceRef] = splitSource(meta.source);

  const rows: {label: string; value: string}[] = [
    {label: '类型', value: meta.kind ? (kindLabel[meta.kind] ?? meta.kind) : '—'},
    {label: '状态', value: meta.status ? (statusLabel[meta.status] ?? meta.status) : '—'},
    {label: '执行模式', value: meta.mode || '—'},
    {label: '模型', value: meta.model || '—'},
    {label: '来源', value: sourceName || '—'},
    {label: '仓库', value: shortPath(meta.repo) || '未映射'},
  ].filter((row) => row.value);

  const rowClass =
    'grid grid-cols-[60px_minmax(0,1fr)] items-start gap-2 border-b border-border/60 py-2 first:pt-0';
  const labelClass = 'pt-px text-[11.5px] text-faint';
  const valueClass = 'min-w-0 break-words text-[12px] leading-[1.55] text-foreground/85';

  return (
    <div className="space-y-4">
      <dl>
        {rows.map((row) => (
          <div key={row.label} className={rowClass}>
            <dt className={labelClass}>{row.label}</dt>
            <dd className={cn(valueClass, row.label === '仓库' && 'font-mono text-[11px]')} title={row.label === '仓库' ? meta.repo : row.value}>
              {row.value}
            </dd>
          </div>
        ))}
        {/* 编号 / ID 这类长串：标签在上、值独占整行，避免在 60px 标签列后被挤成一截 */}
        {sourceRef ? (
          <div key="ref" className="border-b border-border/60 pb-2">
            <dt className={labelClass}>单号</dt>
            <dd className="mt-1 break-all font-mono text-[11px] leading-[1.6] text-foreground/85">{sourceRef}</dd>
          </div>
        ) : null}
        {meta.id ? (
          <div key="id">
            <dt className={labelClass}>ID</dt>
            <dd className="mt-1 break-all font-mono text-[11px] leading-[1.6] text-foreground/85">{meta.id}</dd>
          </div>
        ) : null}
      </dl>
      {worktree ? (
        <div>
          <div className="mb-1.5 text-[11.5px] text-faint">worktree</div>
          <div className="break-all rounded-lg bg-secondary/60 px-2.5 py-2 font-mono text-[10.5px] leading-[1.65] text-foreground/70">
            {worktree}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** 「tapd tapd:story:123」→ ['TAPD', 'tapd:story:123']。 */
function splitSource(source: string): [string, string] {
  const value = (source || '').trim();
  if (!value) return ['', ''];
  const [head, ...rest] = value.split(/\s+/);
  const label = {manual: '手动', tapd: 'TAPD', email: '邮件'}[head] ?? head;
  return [label, rest.join(' ')];
}

/**
 * 仓库路径只显示末尾两级（`…/projects/ai_task`）：
 * 完整路径在 310px 的侧栏里会折成两行，反而更难读；悬停可看全路径。
 */
function shortPath(path: string): string {
  const value = (path || '').trim();
  if (!value || value === '—') return '';
  const parts = value.replace(/\/+$/, '').split('/');
  if (parts.length <= 2) return value;
  return '…/' + parts.slice(-2).join('/');
}

function ReportSync({summary}: {summary: ReportSummary}) {
  const {sync} = summary;
  if (!sync) return null;
  const conflicted = summary.conflicted.length;
  const rows = [
    {label: '已同步', value: summary.syncedCount, tone: 'success' as Tone},
    {label: '冲突', value: conflicted, tone: (conflicted ? 'warning' : 'neutral') as Tone},
    {label: '删除', value: sync.deleted.length, tone: 'neutral' as Tone},
    {label: '跳过', value: sync.skipped.length, tone: 'neutral' as Tone},
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        {rows.map((row) => (
          <div key={row.label} className="rounded-lg bg-secondary/50 px-3 py-2">
            <div className={cn('text-[17px] font-semibold leading-none tracking-[-0.02em]', TONE_TEXT[row.tone])}>
              {row.value}
            </div>
            <div className="mt-1 text-[11px] text-faint">{row.label}</div>
          </div>
        ))}
      </div>
      {sync.error ? (
        <p className="rounded-lg bg-destructive-muted px-2.5 py-2 text-[11.5px] leading-relaxed text-destructive">
          同步错误：{sync.error}
        </p>
      ) : null}
      {sync.backupDir ? (
        <div>
          <div className="mb-1 text-[11.5px] text-faint">备份目录</div>
          <div className="break-all rounded-lg bg-secondary/70 px-2.5 py-2 font-mono text-[10.5px] leading-[1.6] text-foreground/70">
            {sync.backupDir}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** 改动明细：目录条形图（可横向铺满）+ 文件胶囊，文件名过长时省略中段。 */
function ReportChanges({summary}: {summary: ReportSummary}) {
  const {modules, changedFiles} = summary;
  if (!changedFiles.length) {
    return <p className="text-[12px] text-faint">本次执行没有改动任何文件。</p>;
  }
  const tone: Tone = summary.dryRun ? 'info' : 'success';
  return (
    <div className="space-y-4">
      <div className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
        {modules.map((slice) => (
          <div key={slice.name}>
            <div className="mb-1.5 flex items-baseline justify-between gap-3">
              <span className="truncate font-mono text-[11.5px] text-foreground/80" title={slice.name}>
                {slice.name}
              </span>
              <span className="shrink-0 text-[11px] text-faint">{slice.count} 个</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-secondary">
              <div
                className={cn('h-full rounded-full transition-all', TONE_BAR[tone])}
                style={{width: `${Math.max(6, slice.percent)}%`}}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {changedFiles.map((file) => (
          <code
            key={file}
            title={file}
            className="max-w-full truncate rounded-lg border border-border bg-[#fafafd] px-2 py-1 font-mono text-[11px] text-foreground/75"
          >
            {file}
          </code>
        ))}
      </div>
    </div>
  );
}

/** 统一的内容面板：大圆角、无多余阴影，留足内边距。 */
function Panel({
  children,
  className,
  id,
}: {
  children: React.ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section
      id={id}
      className={cn('rounded-2xl border border-border bg-card px-6 py-5 shadow-sm', className)}
    >
      {children}
    </section>
  );
}

function PanelHeading({
  icon: Icon,
  children,
  extra,
}: {
  icon: LucideIcon;
  children: React.ReactNode;
  extra?: React.ReactNode;
}) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2 text-[13.5px] font-semibold text-foreground">
        <Icon className="size-4 text-faint" />
        {children}
      </div>
      {extra}
    </div>
  );
}

function ReportSkeleton() {
  return (
    <div className="space-y-5">
      <div className="h-[84px] animate-pulse rounded-2xl border border-border bg-secondary/40" />
      <div className="h-[92px] animate-pulse rounded-2xl border border-border bg-secondary/30" />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_310px]">
        <div className="h-[420px] animate-pulse rounded-2xl border border-border bg-secondary/30" />
        <div className="hidden h-[320px] animate-pulse rounded-2xl border border-border bg-secondary/30 xl:block" />
      </div>
      <div className="flex items-center justify-center gap-2 text-[12px] text-faint">
        <Loader2 className="size-4 animate-spin" />
        正在载入报告…
      </div>
    </div>
  );
}