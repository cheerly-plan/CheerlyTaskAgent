import * as React from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  ExternalLink,
  CircleCheck,
  CircleSlash,
  Download,
  FileDiff,
  Files,
  FolderGit2,
  FolderTree,
  Timer,
  ListChecks,
  Loader2,
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
    <header className="mb-5 flex items-center justify-between gap-4">
      <div className="flex min-w-0 items-center gap-3.5">
        <IconTip label={`返回${backLabel}`}>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={onBack}
            aria-label={`返回${backLabel}`}
            className="shrink-0 bg-card"
          >
            <ArrowLeft />
          </Button>
        </IconTip>
        <div className="min-w-0">
          <h1 className="truncate text-[23px] font-semibold leading-[1.3] tracking-[-0.02em] text-foreground">
            {title}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2.5 text-[12.5px] text-faint">
            {kind ? <Badge variant="outline" className="bg-card">{kind}</Badge> : null}
            {id ? <span className="font-mono text-[12px]">{id}</span> : null}
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
  // 描述正文按行渲染，保留来源系统里的换行（处理人 / 优先级这类信息已在侧栏）。
  const descriptionHtml = React.useMemo(
    () => (summary.description ? renderMarkdown(summary.description, true) : ''),
    [summary.description],
  );

  return (
    <div className="space-y-6">
      {/* 结论层：执行结果与耗时 / 改动 / 同步并排成一行 */}
      <ReportStats summary={summary} />
      <ReportAttentionList attention={summary.attention} />

      {/* 证据层：左侧一张纸面承载长文，右侧一张信息卡随时可查 */}
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Sheet>
          <SheetSection title="需求描述">
            {descriptionHtml ? (
              <div
                className="report-body text-[13.5px] leading-[1.85]"
                dangerouslySetInnerHTML={{__html: descriptionHtml}}
              />
            ) : (
              <p className="text-[12.5px] text-faint">这个工单没有填写描述。</p>
            )}
          </SheetSection>

          <SheetSection title="执行报告" meta={reportSections(summary)}>
            <div className="report-body" dangerouslySetInnerHTML={{__html: reportHtml}} />
          </SheetSection>

          <SheetSection
            id="report-changes"
            title="改动明细"
            meta={summary.changedFiles.length ? `${summary.changedFiles.length} 个文件` : undefined}
          >
            <ReportChanges summary={summary} />
          </SheetSection>
        </Sheet>

        {/* 不做 sticky：信息卡比视口高，固定后顶部会被裁掉，反而不好读。 */}
        <aside>
          <SidePanel>
            <SideGroup title="工单信息" icon={FolderGit2}>
              <ReportMeta summary={summary} />
            </SideGroup>

            {summary.metaFields.length ? (
              <SideGroup title="来源信息" icon={ListChecks}>
                <MetaFields fields={summary.metaFields} />
              </SideGroup>
            ) : null}

            {summary.sync ? (
              <SideGroup title="同步到主仓库" icon={Files} last>
                <ReportSync summary={summary} />
              </SideGroup>
            ) : null}
          </SidePanel>
        </aside>
      </div>
    </div>
  );
}

/** 报告正文里出现的章节标题，用于给「执行报告」一个概览。 */
function reportSections(summary: ReportSummary): string | undefined {
  const body = summary.rest || '';
  const found = [...body.matchAll(/^##\s+(.+)$/gm)].map((match) => match[1].trim());
  return found.length ? found.slice(0, 3).join(' · ') : undefined;
}

/**
 * 纸面：整个正文区共用一张白色底，内部用细分隔线分区。
 *
 * 之前正文直接铺在灰色页底上、只有几根细线，右栏却是白卡片，
 * 左右两种视觉语言并存，看起来像左半边没做完。
 */
function Sheet({children}: {children: React.ReactNode}) {
  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm">{children}</div>
  );
}

/** 纸面内的一节：标题 + 可选的右侧摘要，节与节之间用分隔线隔开。 */
function SheetSection({
  id,
  title,
  meta,
  children,
}: {
  id?: string;
  title: string;
  meta?: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-6 px-6 py-5 [&+section]:border-t [&+section]:border-border/70 sm:px-7">
      <div className="mb-3.5 flex items-baseline justify-between gap-4">
        <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-foreground">{title}</h2>
        {meta ? <span className="shrink-0 truncate text-[11.5px] text-faint">{meta}</span> : null}
      </div>
      {children}
    </section>
  );
}

/** 信息卡：一整张卡片里分三组，比三张独立小卡更安静、更好扫读。 */
function SidePanel({children}: {children: React.ReactNode}) {
  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm">{children}</div>
  );
}

function SideGroup({
  title,
  icon: Icon,
  last,
  children,
}: {
  title: string;
  icon: LucideIcon;
  last?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className={cn('px-5 py-4', !last && 'border-b border-border/70')}>
      <div className="mb-3 flex items-center gap-2 text-[12.5px] font-semibold text-foreground">
        <Icon className="size-4 text-faint" />
        {title}
      </div>
      {children}
    </section>
  );
}

/** 来源描述里提取出的元信息：标签在上、值在下，链接可点。 */
function MetaFields({fields}: {fields: ReportSummary['metaFields']}) {
  return (
    <dl className="space-y-2.5">
      {fields.map((field) => (
        <div key={`${field.label}-${field.value}`} className="min-w-0">
          <dt className="text-[11.5px] text-faint">{field.label}</dt>
          <dd className="mt-1 break-all text-[12.5px] leading-[1.55] text-foreground/85">
            {field.url ? (
              <a
                href={field.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-baseline gap-1 text-primary hover:underline"
                title={field.url}
              >
                打开链接
                <ExternalLink className="size-3 self-center" />
              </a>
            ) : (
              field.value
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * 指标行：执行结果 / 耗时 / 改动文件 / 同步主仓库。
 *
 * 执行结果原本是通栏大横幅，占掉近一屏；并进这一行后既保留醒目度，
 * 又把首屏让给正文。
 */
function ReportStats({summary}: {summary: ReportSummary}) {
  const tone: Tone = summary.outcome === 'success' ? 'success' : summary.outcome === 'failure' ? 'danger' : 'neutral';
  const Icon = tone === 'success' ? CircleCheck : tone === 'danger' ? AlertTriangle : CircleSlash;
  const conflicts = summary.conflicted.length;

  const cells: {label: string; value: string; hint?: string; tone: Tone; icon: LucideIcon}[] = [
    {
      label: '执行结果',
      value: summary.outcome === 'success' ? '执行成功' : summary.outcome === 'failure' ? '执行未通过' : '尚未执行',
      hint: summary.dryRun === null ? undefined : summary.dryRun ? 'dry-run 试运行' : 'apply 已落地',
      tone,
      icon: Icon,
    },
    {label: '耗时', value: summary.duration ?? '—', hint: '执行器总耗时', tone: 'neutral', icon: Timer},
    {
      label: '改动文件',
      value: String(summary.changedFiles.length),
      hint: summary.modules.length ? `分布在 ${summary.modules.length} 个模块` : undefined,
      tone: summary.changedFiles.length ? 'info' : 'neutral',
      icon: Files,
    },
    {
      label: '同步主仓库',
      value: String(summary.syncedCount),
      hint: conflicts ? `${conflicts} 个冲突待处理` : summary.syncedCount ? '已全部写入' : undefined,
      tone: conflicts ? 'warning' : summary.syncedCount ? 'success' : 'neutral',
      icon: FolderTree,
    },
  ];

  return (
    <div className="grid grid-cols-2 divide-border overflow-hidden rounded-2xl border border-border bg-card shadow-sm sm:grid-cols-4 sm:divide-x">
      {cells.map((cell, index) => (
        <div
          key={cell.label}
          className={cn(
            'px-5 py-4',
            index >= 2 && 'border-t border-border sm:border-t-0',
            index % 2 === 1 && 'border-l border-border',
            index === 2 && 'sm:border-l',
          )}
        >
          {/* 四个格子统一用线条图标 + 语义色，执行结果不再单独用实心色块 */}
          <div className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
            <cell.icon
              className={cn('size-4 shrink-0', cell.tone === 'neutral' ? 'text-muted-foreground' : TONE_TEXT[cell.tone])}
            />
            {cell.label}
          </div>
          <div
            className={cn(
              'mt-2 truncate font-semibold leading-none tracking-[-0.03em]',
              index === 0 ? 'text-[22px]' : 'text-[27px]',
              TONE_TEXT[cell.tone],
            )}
          >
            {cell.value}
          </div>
          {cell.hint ? <div className="mt-2 truncate text-[11.5px] text-faint">{cell.hint}</div> : null}
        </div>
      ))}
    </div>
  );
}

function ReportAttentionList({attention}: {attention: Attention[]}) {
  if (!attention.length) {
    return (
      <p className="flex items-center gap-2 px-1 text-[12.5px] text-faint">
        <CircleCheck className="size-4 text-success/80" />
        没有需要特别处理的事项
      </p>
    );
  }

  return (
    <section>
      <ul className="grid gap-3 md:grid-cols-2">
        {attention.map((entry) => {
          const Icon = ATTENTION_ICON[entry.kind];
          return (
            <li
              key={`${entry.kind}-${entry.title}`}
              className={cn(
                'rounded-xl border border-border/70 border-l-[3px] px-4 py-3.5',
                ALERT_ACCENT[entry.tone],
                ALERT_BG[entry.tone],
              )}
            >
              <div className="flex items-start gap-3">
                <Icon className={cn('mt-0.5 size-[18px] shrink-0', TONE_TEXT[entry.tone])} />
                <div className="min-w-0 flex-1">
                  <div className={cn('text-[13px] font-semibold', TONE_TEXT[entry.tone])}>{entry.title}</div>
                  {entry.detail ? (
                    <p className="mt-1.5 break-words text-[12px] leading-relaxed text-muted-foreground">{entry.detail}</p>
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
    'grid grid-cols-[62px_minmax(0,1fr)] items-start gap-2 border-b border-border/60 py-2.5 first:pt-0';
  const labelClass = 'pt-px text-[12px] text-faint';
  const valueClass = 'min-w-0 break-words text-[12.5px] leading-[1.55] text-foreground/85';

  return (
    <div className="space-y-4">
      <dl>
        {rows.map((row) => (
          <div key={row.label} className={rowClass}>
            <dt className={labelClass}>{row.label}</dt>
            <dd className={cn(valueClass, row.label === '仓库' && 'font-mono text-[11.5px]')} title={row.label === '仓库' ? meta.repo : row.value}>
              {row.value}
            </dd>
          </div>
        ))}
        {/* 编号 / ID 这类长串：标签在上、值独占整行，避免在 60px 标签列后被挤成一截 */}
        {sourceRef ? (
          <div key="ref" className="border-b border-border/60 pb-2">
            <dt className={labelClass}>单号</dt>
            <dd className="mt-1.5 break-all font-mono text-[11.5px] leading-[1.6] text-foreground/85">{sourceRef}</dd>
          </div>
        ) : null}
        {meta.id ? (
          <div key="id">
            <dt className={labelClass}>ID</dt>
            <dd className="mt-1.5 break-all font-mono text-[11.5px] leading-[1.6] text-foreground/85">{meta.id}</dd>
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
          <div key={row.label} className="rounded-lg bg-secondary/60 px-3 py-2.5">
            <div className={cn('text-[19px] font-semibold leading-none tracking-[-0.02em]', TONE_TEXT[row.tone])}>
              {row.value}
            </div>
            <div className="mt-1.5 text-[11.5px] text-faint">{row.label}</div>
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
          <div className="mb-1.5 text-[12px] text-faint">备份目录</div>
          <div className="break-all rounded-lg bg-secondary/70 px-2.5 py-2 font-mono text-[11px] leading-[1.6] text-foreground/70">
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
              <span className="truncate font-mono text-[12px] text-foreground/80" title={slice.name}>
                {slice.name}
              </span>
              <span className="shrink-0 text-[11.5px] text-faint">{slice.count} 个</span>
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