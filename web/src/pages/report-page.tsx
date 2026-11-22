import * as React from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  CircleCheck,
  CircleSlash,
  Download,
  ExternalLink,
  Loader2,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';

import {useApp} from '@/app-context';
import {Button} from '@/components/ui/button';
import {Tooltip, TooltipContent, TooltipTrigger} from '@/components/ui/tooltip';
import {renderMarkdown} from '@/lib/markdown';
import {kindLabel, statusLabel} from '@/lib/labels';
import type {Attention, ReportSummary, Tone} from '@/lib/report-view';
import {cn} from '@/lib/utils';

/**
 * 报告详情页（文档式）。
 *
 * 版面：页头 → 一条状态带（成没成 + 耗时/改动/同步）→ 左侧一张纸承载正文、
 * 右侧纯文本元信息。
 *
 * 刻意只保留两处「容器」：正文纸面与状态带。之前每个区块都是一张同尺寸、
 * 同圆角的白卡，四块并列时没有主次，灰底上浮着一堆盒子，反而显得空。
 * 右侧信息不做卡片，只用小标题 + 键值行，视觉上退到背景里。
 */

const SHEET = 'rounded-2xl border border-border bg-card px-7 py-6';

const TONE_TEXT: Record<Tone, string> = {
  success: 'text-success-foreground',
  warning: 'text-warning-foreground',
  danger: 'text-destructive',
  info: 'text-info-foreground',
  neutral: 'text-foreground',
};

const ALERT_BORDER: Record<Tone, string> = {
  success: 'border-l-success',
  warning: 'border-l-warning',
  danger: 'border-l-destructive',
  info: 'border-l-info',
  neutral: 'border-l-border',
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

  // 正文从「需求描述」之后开始：标题与前两张主表已结构化展示在别处。
  const reportHtml = React.useMemo(
    () => renderMarkdown(summary?.rest?.trim() ? summary.rest : (markdown ?? '')),
    [summary, markdown],
  );

  return (
    <div className="mx-auto w-full max-w-[1140px]">
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
        <div className="flex items-center gap-2.5 rounded-xl border border-destructive/25 bg-destructive-muted/60 px-5 py-4 text-[13px] text-destructive">
          <AlertTriangle className="size-4 shrink-0" />
          打开报告失败：{error}
        </div>
      ) : summary ? (
        <ReportView summary={summary} reportHtml={reportHtml} />
      ) : (
        <section className={SHEET}>
          <div className="report-body" dangerouslySetInnerHTML={{__html: reportHtml}} />
        </section>
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
    <header className="mb-5 flex items-center gap-3">
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
        <h1 className="truncate text-[24px] font-semibold leading-[1.25] tracking-[-0.02em] text-foreground">
          {title}
        </h1>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[12px] text-faint">
          {kind ? <span>{kind}</span> : null}
          {kind ? <span className="text-border">·</span> : null}
          {id ? <span className="font-mono text-[11.5px]">{id}</span> : null}
          <span className="text-border">·</span>
          <button type="button" onClick={onBack} className="hover:text-foreground hover:underline">
            {backLabel}
          </button>
        </div>
      </div>

      {downloadUrl ? (
        <Button variant="outline" size="sm" className="ml-auto shrink-0 bg-card" asChild>
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
  // 描述正文按行渲染，保留来源系统的换行（处理人 / 优先级已在右栏）。
  const descriptionHtml = React.useMemo(
    () => (summary.description ? renderMarkdown(summary.description, true) : ''),
    [summary.description],
  );

  return (
    <div>
      <StatusBand summary={summary} />
      <AttentionRows attention={summary.attention} />

      <div className="grid items-start gap-9 xl:grid-cols-[minmax(0,1fr)_264px]">
        <section className={SHEET}>
          <DocSection label="需求描述">
            {descriptionHtml ? (
              <div
                className="report-body text-[13.5px] leading-[1.85]"
                dangerouslySetInnerHTML={{__html: descriptionHtml}}
              />
            ) : (
              <p className="text-[12.5px] text-faint">这个工单没有填写描述。</p>
            )}
          </DocSection>

          <DocSection label="执行报告" meta={reportSections(summary)}>
            <div className="report-body" dangerouslySetInnerHTML={{__html: reportHtml}} />
          </DocSection>

          <DocSection
            label="改动明细"
            meta={summary.changedFiles.length ? `${summary.changedFiles.length} 个文件` : undefined}
          >
            <ChangesBlock summary={summary} />
          </DocSection>
        </section>

        {/* 信息量已压到一屏内，宽屏下固定，滚动长文时元信息始终可见 */}
        <aside className="space-y-7 pt-1 xl:sticky xl:top-0">
          <SideBlock label="工单信息">
            <MetaRows summary={summary} />
          </SideBlock>

          {summary.metaFields.length ? (
            <SideBlock label="来源信息">
              <SourceRows fields={summary.metaFields} />
            </SideBlock>
          ) : null}

          {summary.sync ? (
            <SideBlock label="同步到主仓库">
              <SyncRows summary={summary} />
            </SideBlock>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

/**
 * 状态带：一条横栏交代「成没成 + 关键数字」。
 *
 * 相比通栏大横幅，它不抢正文；相比四格指标卡，它不会被误读成表格表头。
 */
function StatusBand({summary}: {summary: ReportSummary}) {
  const tone: Tone = summary.outcome === 'success' ? 'success' : summary.outcome === 'failure' ? 'danger' : 'neutral';
  const Icon: LucideIcon = tone === 'success' ? CircleCheck : tone === 'danger' ? AlertTriangle : CircleSlash;

  return (
    <div
      className={cn(
        'mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border px-5 py-3.5',
        tone === 'success' && 'border-success/25 bg-success-muted',
        tone === 'danger' && 'border-destructive/25 bg-destructive-muted',
        tone === 'neutral' && 'border-border bg-secondary/60',
      )}
    >
      <Icon className={cn('size-5 shrink-0', TONE_TEXT[tone])} />
      <span className={cn('text-[15px] font-semibold', TONE_TEXT[tone])}>
        {summary.outcome === 'success' ? '执行成功' : summary.outcome === 'failure' ? '执行未通过' : '尚未执行'}
      </span>
      {summary.meta.mode ? (
        <span className="rounded-md bg-white/75 px-2 py-0.5 text-[11.5px] font-medium text-[#6d3fc0]">
          {summary.dryRun ? 'dry-run 试运行' : 'apply 已落地'}
        </span>
      ) : null}

      <div className="ml-auto flex flex-wrap items-center gap-x-5 gap-y-1 text-[12px] text-muted-foreground">
        <span>
          耗时 <b className="font-semibold text-foreground">{summary.duration ?? '—'}</b>
        </span>
        <span>
          改动 <b className="font-semibold text-foreground">{summary.changedFiles.length}</b>
        </span>
        <span>
          同步 <b className="font-semibold text-foreground">{summary.syncedCount}</b>
        </span>
      </div>
    </div>
  );
}

/** 需要注意的事项：只在有风险时出现，按色带区分等级。 */
function AttentionRows({attention}: {attention: Attention[]}) {
  if (!attention.length) return null;

  return (
    <ul className="mb-5 space-y-2">
      {attention.map((entry) => {
        const Icon = entry.tone === 'danger' ? AlertTriangle : TriangleAlert;
        const target = entry.kind === 'files' ? 'report-changes' : null;
        return (
          <li
            key={`${entry.kind}-${entry.title}`}
            className={cn(
              'rounded-lg border border-border/70 border-l-[3px] bg-card px-4 py-2.5',
              ALERT_BORDER[entry.tone],
            )}
          >
            <div className="flex items-start gap-2.5">
              <Icon className={cn('mt-0.5 size-4 shrink-0', TONE_TEXT[entry.tone])} />
              <div className="min-w-0 flex-1">
                <div className={cn('text-[12.5px] font-semibold', TONE_TEXT[entry.tone])}>{entry.title}</div>
                {entry.detail ? (
                  <p className="mt-0.5 break-words text-[12px] leading-relaxed text-muted-foreground">{entry.detail}</p>
                ) : null}
                {entry.items?.length ? (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {entry.items.slice(0, 6).map((file) => (
                      <code
                        key={file}
                        className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10.5px] text-foreground/70"
                      >
                        {file}
                      </code>
                    ))}
                    {entry.more ? <span className="self-center text-[10.5px] text-faint">等 {entry.more} 个</span> : null}
                  </div>
                ) : null}
                {target ? (
                  <button
                    type="button"
                    onClick={() => document.getElementById(target)?.scrollIntoView({behavior: 'smooth'})}
                    className="mt-1 text-[11px] font-medium text-primary hover:underline"
                  >
                    查看改动文件
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

/** 报告正文里出现的章节标题，用于给「执行报告」一个概览。 */
function reportSections(summary: ReportSummary): string | undefined {
  const found = [...(summary.rest || '').matchAll(/^##\s+(.+)$/gm)].map((match) => match[1].trim());
  return found.length ? found.slice(0, 3).join(' · ') : undefined;
}

/** 纸面内的一节：小标签 + 内容，节与节之间用一条细线分开。 */
function DocSection({
  label,
  meta,
  children,
}: {
  label: string;
  meta?: string;
  children: React.ReactNode;
}) {
  return (
    <section id={label === '改动明细' ? 'report-changes' : undefined} className="scroll-mt-6 [&+section]:mt-7 [&+section]:border-t [&+section]:border-border [&+section]:pt-6">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 className="text-[12px] font-semibold tracking-[0.08em] text-faint">{label}</h2>
        {meta ? <span className="shrink-0 truncate text-[11.5px] text-faint">{meta}</span> : null}
      </div>
      {children}
    </section>
  );
}

/** 右栏分组：无边框、无底色，只有小标题 + 键值行。 */
function SideBlock({label, children}: {label: string; children: React.ReactNode}) {
  return (
    <section>
      <h2 className="mb-2.5 text-[11px] font-semibold tracking-[0.08em] text-faint">{label}</h2>
      {children}
    </section>
  );
}

/** 键值行：标签左、值右，值可换行。 */
function Row({label, value, mono}: {label: string; value: string; mono?: boolean}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-[5px] text-[12px]">
      <dt className="shrink-0 text-faint">{label}</dt>
      <dd
        className={cn('min-w-0 break-words text-right text-foreground/85', mono && 'font-mono text-[11px]')}
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}

function MetaRows({summary}: {summary: ReportSummary}) {
  const {meta} = summary;
  const worktree = meta.worktree && meta.worktree !== '—' ? meta.worktree : '';
  const [sourceName, sourceRef] = splitSource(meta.source);

  return (
    <dl className="space-y-0">
      <Row label="类型" value={meta.kind ? (kindLabel[meta.kind] ?? meta.kind) : '—'} />
      <Row label="状态" value={meta.status ? (statusLabel[meta.status] ?? meta.status) : '—'} />
      <Row label="执行模式" value={meta.mode || '—'} />
      <Row label="模型" value={meta.model || '—'} />
      <Row label="来源" value={sourceName || '—'} />
      <Row label="仓库" value={shortPath(meta.repo) || '未映射'} mono />
      {sourceRef ? <Row label="单号" value={sourceRef} mono /> : null}
      {meta.id ? <Row label="ID" value={meta.id} mono /> : null}
      {worktree ? (
        <div className="pt-2">
          <div className="text-[11px] text-faint">worktree</div>
          <div className="mt-1 break-all font-mono text-[10.5px] leading-[1.6] text-foreground/60">{worktree}</div>
        </div>
      ) : null}
    </dl>
  );
}

function SourceRows({fields}: {fields: ReportSummary['metaFields']}) {
  return (
    <dl>
      {fields.map((field) => (
        <div key={`${field.label}-${field.value}`} className="flex items-baseline justify-between gap-3 py-[5px] text-[12px]">
          <dt className="shrink-0 text-faint">{field.label}</dt>
          <dd className="min-w-0 break-words text-right text-foreground/85">
            {field.url ? (
              <a
                href={field.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-0.5 text-primary hover:underline"
              >
                打开链接
                <ExternalLink className="size-3" />
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

function SyncRows({summary}: {summary: ReportSummary}) {
  const {sync} = summary;
  if (!sync) return null;
  return (
    <dl>
      <Row label="已同步" value={`${summary.syncedCount} 个`} />
      <Row label="冲突" value={`${summary.conflicted.length} 个`} />
      <Row label="删除" value={`${sync.deleted.length} 个`} />
      <Row label="跳过" value={`${sync.skipped.length} 个`} />
      {sync.error ? (
        <p className="mt-2 text-[11.5px] leading-relaxed text-destructive">同步错误：{sync.error}</p>
      ) : null}
      {sync.backupDir ? (
        <div className="pt-2">
          <div className="text-[11px] text-faint">备份目录</div>
          <div className="mt-1 break-all font-mono text-[10.5px] leading-[1.6] text-foreground/60">{sync.backupDir}</div>
        </div>
      ) : null}
    </dl>
  );
}

/** 改动明细：目录条形图 + 文件胶囊。 */
function ChangesBlock({summary}: {summary: ReportSummary}) {
  const {modules, changedFiles} = summary;
  if (!changedFiles.length) {
    return <p className="text-[12.5px] text-faint">本次执行没有改动任何文件。</p>;
  }
  const tone: Tone = summary.dryRun ? 'info' : 'success';

  return (
    <div className="space-y-4">
      <div className="grid gap-x-8 gap-y-2.5 sm:grid-cols-2">
        {modules.map((slice) => (
          <div key={slice.name}>
            <div className="mb-1.5 flex items-baseline justify-between gap-3">
              <span className="truncate font-mono text-[11.5px] text-foreground/75" title={slice.name}>
                {slice.name}
              </span>
              <span className="shrink-0 text-[11px] text-faint">{slice.count} 个</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
              <div
                className={cn('h-full rounded-full', tone === 'info' ? 'bg-info' : 'bg-success')}
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
            className="max-w-full truncate rounded-md border border-border bg-[#fafafd] px-2 py-0.5 font-mono text-[11px] text-foreground/70"
          >
            {file}
          </code>
        ))}
      </div>
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

/** 仓库路径只显示末尾两级，完整路径悬停可见。 */
function shortPath(path: string): string {
  const value = (path || '').trim();
  if (!value || value === '—') return '';
  const parts = value.replace(/\/+$/, '').split('/');
  if (parts.length <= 2) return value;
  return '…/' + parts.slice(-2).join('/');
}

function ReportSkeleton() {
  return (
    <div>
      <div className="mb-5 h-[58px] animate-pulse rounded-xl border border-border bg-secondary/50" />
      <div className="grid gap-9 xl:grid-cols-[minmax(0,1fr)_264px]">
        <div className="h-[420px] animate-pulse rounded-2xl border border-border bg-secondary/40" />
        <div className="hidden h-[300px] animate-pulse rounded-xl bg-secondary/30 xl:block" />
      </div>
      <div className="mt-4 flex items-center justify-center gap-2 text-[12px] text-faint">
        <Loader2 className="size-4 animate-spin" />
        正在载入报告…
      </div>
    </div>
  );
}