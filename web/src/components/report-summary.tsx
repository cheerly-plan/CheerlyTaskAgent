import {
  AlertTriangle,
  ChevronDown,
  CircleCheck,
  CircleSlash,
  FileDiff,
  Files,
  FolderTree,
  ListChecks,
  Timer,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';

import {Badge} from '@/components/ui/badge';
import {cn} from '@/lib/utils';
import {kindLabel, statusLabel} from '@/lib/labels';
import type {Attention, ModuleSlice, ReportSummary, Tone} from '@/lib/report-view';

/**
 * 报告弹窗顶部的「一眼看清」区：结论横幅 → 指标卡 → 需要注意 → 核心信息。
 * 只做展示，所有数据由 `analyzeReport()` 提供，组件本身不解析 Markdown。
 */

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

/** 报告结论横幅：颜色 + 图标 + 一句话结论，替代原来埋在表格里的「成功 | 是」。 */
export function ReportOutcomeBanner({
  summary,
  onJumpTo,
}: {
  summary: ReportSummary;
  onJumpTo: (tab: 'attention' | 'changes' | 'full') => void;
}) {
  const tone: Tone = summary.outcome === 'success' ? 'success' : summary.outcome === 'failure' ? 'danger' : 'neutral';
  const Icon = tone === 'success' ? CircleCheck : tone === 'danger' ? AlertTriangle : CircleSlash;
  const conflicts = summary.conflicted.length;

  return (
    <div
      className={cn(
        'flex items-start gap-3 rounded-xl border p-3.5',
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
        {conflicts ? (
          <button
            type="button"
            onClick={() => onJumpTo('attention')}
            className="mt-1.5 text-xs font-medium text-[#a4610a] underline-offset-2 hover:underline"
          >
            有 {conflicts} 个文件同步冲突，查看处理建议 →
          </button>
        ) : summary.sync?.error ? (
          <button
            type="button"
            onClick={() => onJumpTo('attention')}
            className="mt-1.5 text-xs font-medium text-destructive underline-offset-2 hover:underline"
          >
            同步到主仓库出错，查看详情 →
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** 四张指标卡：执行结果 / 耗时 / 改动文件 / 同步数（与概览页 KPI 卡同一套视觉）。 */
export function ReportStats({stats}: {stats: ReportSummary['stats']}) {
  const icons: LucideIcon[] = [CircleCheck, Timer, Files, FolderTree];
  return (
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      {stats.map((stat, index) => {
        const Icon = icons[index] ?? CircleCheck;
        return (
          <div key={stat.label} className="rounded-xl border border-border bg-card px-3 py-2.5 shadow-xs">
            <div className="flex items-center gap-1.5">
              <span className={cn('grid size-5 shrink-0 place-items-center rounded-md', TONE_ICON_BG[stat.tone])}>
                <Icon className="size-3" />
              </span>
              <span className="truncate text-[11px] text-faint">{stat.label}</span>
            </div>
            <div className={cn('mt-1 truncate text-lg font-semibold leading-tight tracking-[-0.02em]', TONE_TEXT[stat.tone])}>
              {stat.value}
            </div>
            {stat.hint ? <div className="truncate text-[10.5px] text-faint">{stat.hint}</div> : null}
          </div>
        );
      })}
    </div>
  );
}

/** 需要注意：错误 / 冲突 / 截断 / 改动文件，把关键风险前置。 */
export function ReportAttention({
  attention,
  onJumpTo,
}: {
  attention: Attention[];
  onJumpTo: (tab: 'changes' | 'full') => void;
}) {
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
        // 结论类提示的去处：冲突看备份目录、改动看文件清单、其余看完整报告。
        const Icon = ATTENTION_ICON[entry.kind];
        const target = entry.kind === 'files' ? 'changes' : entry.kind === 'warning' ? 'full' : null;
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
                    {entry.items.slice(0, 6).map((item) => (
                      <code key={item} className="rounded bg-secondary px-1.5 py-px font-mono text-[10.5px] text-foreground/75">
                        {item}
                      </code>
                    ))}
                    {entry.more ? <span className="text-[10.5px] text-faint">等 {entry.more} 个</span> : null}
                  </div>
                ) : null}
                {target ? (
                  <button
                    type="button"
                    onClick={() => onJumpTo(target)}
                    className="mt-1 text-[11px] font-medium text-primary underline-offset-2 hover:underline"
                  >
                    查看完整报告 →
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

/** 核心信息：来源 / 类型 / 模型 / 仓库等，两列网格代替原来的 8 行窄表格。 */
export function ReportMeta({summary, onJumpTo}: {summary: ReportSummary; onJumpTo: (tab: 'full') => void}) {
  const basic = summary.groups.find((group) => group.label === '基本信息') ?? summary.groups[0];
  const facts = basic?.facts.filter((fact) => fact.label !== 'worktree') ?? [];

  if (!facts.length) return null;

  return (
    <div className="rounded-xl border border-border bg-[#fcfcfe] px-3 py-2.5">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5 text-xs font-semibold text-foreground">
          核心信息
          <span className="truncate font-normal text-faint">· {summary.subject || '未命名工单'}</span>
        </div>
        <button
          type="button"
          onClick={() => onJumpTo('full')}
          className="flex shrink-0 items-center gap-0.5 text-[11px] font-medium text-primary hover:underline"
        >
          全部字段
          <ChevronDown className="size-3 -rotate-90" />
        </button>
      </div>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2">
        {facts.map((fact) => (
          <div key={fact.label} className="flex min-w-0 items-baseline gap-2 text-xs">
            <dt className="w-[68px] shrink-0 text-faint">{fact.label}</dt>
            <dd
              className={cn(
                'min-w-0 flex-1 truncate text-foreground/85',
                (fact.label === '状态' || fact.label === '类型') && 'font-medium',
                fact.mono && 'font-mono text-[11px]',
              )}
              title={fact.value}
            >
              {displayValue(fact.label, fact.value)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** 状态 / 类型用中文标签，其余原样展示。 */
function displayValue(label: string, value: string): string {
  if (label === '状态') return statusLabel[value] ?? value;
  if (label === '类型') return kindLabel[value] ?? value;
  return value;
}

/** 改动分布：按目录聚合的条形图 + 文件清单，替代纯文本列表。 */
export function ReportChanges({summary}: {summary: ReportSummary}) {
  const {modules, changedFiles} = summary;
  if (!changedFiles.length) {
    return <p className="text-xs text-faint">本次执行没有改动任何文件。</p>;
  }
  const tone: Tone = summary.dryRun ? 'info' : 'success';
  return (
    <div className="space-y-2.5">
      <div className="space-y-1.5">
        {modules.map((slice) => (
          <ModuleBar key={slice.name} slice={slice} tone={tone} />
        ))}
      </div>
      <div className="flex flex-wrap gap-1">
        {changedFiles.map((file) => (
          <code
            key={file}
            title={file}
            className="max-w-full truncate rounded-md border border-border bg-[#fafafd] px-1.5 py-0.5 font-mono text-[10.5px] text-foreground/75"
          >
            {file}
          </code>
        ))}
      </div>
    </div>
  );
}

function ModuleBar({slice, tone}: {slice: ModuleSlice; tone: Tone}) {
  return (
    <div title={slice.files.join('\n')}>
      <div className="mb-0.5 flex items-baseline justify-between gap-2 text-[11.5px]">
        <span className="truncate font-mono text-foreground/80">{slice.name}</span>
        <span className="shrink-0 text-faint">{slice.count} 个</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
        <div className={cn('h-full rounded-full', TONE_BAR[tone])} style={{width: `${Math.max(6, slice.percent)}%`}} />
      </div>
    </div>
  );
}