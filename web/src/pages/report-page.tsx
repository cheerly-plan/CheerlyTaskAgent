import * as React from 'react';
import {toast} from 'sonner';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleCheck,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Info,
  Layers,
  Loader2,
  Play,
  RotateCw,
  Search,
  X,
  type LucideIcon,
} from 'lucide-react';

import {useApp} from '@/app-context';
import type {ReportLayout} from '@/components/report-overlay';
import {renderMarkdown} from '@/lib/markdown';
import {kindLabel, statusLabel} from '@/lib/labels';
import type {Kind} from '@/lib/api';
import {extractReportExtras} from '@/lib/report-view';
import type {ReportAppendix} from '@/lib/report-view';
import {cn} from '@/lib/utils';

/**
 * 报告工作台：顶栏 + Hero + 左侧目录 + 七个章节 + 查找 / 确认浮层。
 * 与 prototypes/report-workbench.html 的交互一一对应；数据全部来自真实报告。
 */

const SECTIONS = [
  {key: 'overview', label: '概览'},
  {key: 'attention', label: '风险提示'},
  {key: 'changes', label: '改动文件'},
  {key: 'sync', label: '同步结果'},
  {key: 'output', label: '执行输出'},
  {key: 'commands', label: '命令与提示词'},
  {key: 'meta', label: '工单元信息'},
] as const;

type SectionKey = (typeof SECTIONS)[number]['key'];

async function copyText(text: string, message = '已复制到剪贴板') {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(message);
  } catch {
    const area = document.createElement('textarea');
    area.value = text;
    area.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand('copy');
      toast.success(message);
    } catch {
      toast.error('复制失败');
    }
    area.remove();
  }
}

interface ReportPageProps {
  layout: ReportLayout;
  onLayoutChange: (layout: ReportLayout) => void;
  onClose: () => void;
}

export function ReportPage({layout, onLayoutChange, onClose}: ReportPageProps) {
  const {reportView, runItemAction} = useApp();
  const {loading, error, summary, markdown, item, downloadUrl, downloadName, backLabel} = reportView;
  const extras = React.useMemo(() => extractReportExtras(markdown ?? ''), [markdown]);

  const descriptionHtml = React.useMemo(
    () => (summary?.description ? renderMarkdown(summary.description, true) : ''),
    [summary],
  );

  // 报告内主操作：dry-run → apply；失败/取消 → 重试；已落地 → 重新执行。
  const primary = React.useMemo(() => {
    if (!item) return null;
    if (item.status === 'succeeded' && item.exec_mode === 'dry-run') {
      return {
        action: 'apply' as const,
        label: '执行 apply',
        confirm: '确认执行 apply？',
        desc: '将按本次 dry-run 的项目与模型真实改代码，在隔离 worktree 中执行并同步到主仓库。',
      };
    }
    if (item.status === 'failed' || item.status === 'cancelled') {
      return {
        action: 'exec' as const,
        label: '重试',
        confirm: '确认重试该任务？',
        desc: '将以相同配置重新入队执行。建议先阅读下方执行错误，否则可能再次失败。',
      };
    }
    if (item.status === 'succeeded' && item.exec_mode === 'apply') {
      return {
        action: 'exec' as const,
        label: '重新执行',
        confirm: '确认重新执行该任务？',
        desc: '将清空当前报告并重新入队，执行结束后同名报告会被新结果覆盖。此操作会真实改动代码。',
      };
    }
    return null;
  }, [item]);

  const [findOpen, setFindOpen] = React.useState(false);
  const [findQuery, setFindQuery] = React.useState('');
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [view, setView] = React.useState<'structured' | 'raw'>('structured');
  const [progress, setProgress] = React.useState(0);
  const [miniTitle, setMiniTitle] = React.useState(false);
  const [active, setActive] = React.useState<SectionKey>('overview');
  const [collapsed, setCollapsed] = React.useState<Partial<Record<SectionKey, boolean>>>({});
  const [fileFilter, setFileFilter] = React.useState('');
  const [openFiles, setOpenFiles] = React.useState<Record<string, boolean>>({});
  const [expandedCode, setExpandedCode] = React.useState<Record<string, boolean>>({});

  const scrollRef = React.useRef<HTMLDivElement>(null);
  const bodyRef = React.useRef<HTMLDivElement>(null);
  const findInputRef = React.useRef<HTMLInputElement>(null);
  const sectionRefs = React.useRef<Partial<Record<SectionKey, HTMLElement | null>>>({});
  // 程序化平滑滚动期间暂停目录高亮的滚动联动，避免途经章节把高亮抢走造成闪烁。
  const programmaticScrollRef = React.useRef(false);
  const scrollEndTimerRef = React.useRef<number | null>(null);

  const lockProgrammaticScroll = React.useCallback(() => {
    programmaticScrollRef.current = true;
    if (scrollEndTimerRef.current !== null) window.clearTimeout(scrollEndTimerRef.current);
    const root = scrollRef.current;
    const done = () => {
      if (!programmaticScrollRef.current) return;
      programmaticScrollRef.current = false;
      root?.removeEventListener('scrollend', done);
      if (scrollEndTimerRef.current !== null) window.clearTimeout(scrollEndTimerRef.current);
      scrollEndTimerRef.current = null;
    };
    root?.addEventListener('scrollend', done);
    scrollEndTimerRef.current = window.setTimeout(done, 900);
  }, []);

  const openFind = React.useCallback(() => {
    setFindOpen(true);
    window.setTimeout(() => findInputRef.current?.focus(), 60);
  }, []);
  const closeFind = React.useCallback(() => {
    setFindOpen(false);
    setFindQuery('');
  }, []);

  // ESC 两级关闭：确认弹层 → 查找条 → 报告；⌘F / Ctrl+F 打开查找。
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'f') {
        event.preventDefault();
        openFind();
        return;
      }
      if (event.key === 'Escape') {
        if (confirmOpen) {
          setConfirmOpen(false);
        } else if (findOpen) {
          closeFind();
        } else {
          onClose();
        }
        event.stopPropagation();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [confirmOpen, findOpen, closeFind, openFind, onClose]);

  // 查找：用 CSS Custom Highlight API 高亮文本节点，不改写报告 DOM。
  const findRangesRef = React.useRef<Range[]>([]);
  const [matchIndex, setMatchIndex] = React.useState(0);
  React.useEffect(() => {
    const api = CSS as unknown as {highlights?: Map<string, Highlight>};
    if (!api.highlights || !bodyRef.current) return;
    api.highlights.delete('report-find');
    api.highlights.delete('report-find-current');
    findRangesRef.current = [];
    setMatchIndex(0);
    const query = findQuery.trim();
    if (!query) return;

    const walker = document.createTreeWalker(bodyRef.current, NodeFilter.SHOW_TEXT);
    const needle = query.toLowerCase();
    let node: Node | null;
    while ((node = walker.nextNode())) {
      const text = node.textContent ?? '';
      const lower = text.toLowerCase();
      let from = 0;
      let at = lower.indexOf(needle, from);
      while (at !== -1) {
        const range = document.createRange();
        range.setStart(node, at);
        range.setEnd(node, at + query.length);
        findRangesRef.current.push(range);
        from = at + query.length;
        at = lower.indexOf(needle, from);
      }
    }
    if (findRangesRef.current.length) {
      const HL = (window as unknown as {Highlight: new (...ranges: Range[]) => Highlight}).Highlight;
      api.highlights.set('report-find', new HL(...findRangesRef.current));
      api.highlights.set('report-find-current', new HL(findRangesRef.current[0]));
    } else {
      setMatchIndex(-1);
    }
    return () => {
      api.highlights?.delete('report-find');
      api.highlights?.delete('report-find-current');
    };
  }, [findQuery, view, summary, markdown]);

  const jumpMatch = (step: number) => {
    const ranges = findRangesRef.current;
    if (!ranges.length) return;
    const next = (matchIndex + step + ranges.length) % ranges.length;
    setMatchIndex(next);
    const api = CSS as unknown as {highlights?: Map<string, Highlight>};
    if (api.highlights) {
      const HL = (window as unknown as {Highlight: new (...ranges: Range[]) => Highlight}).Highlight;
      api.highlights.set('report-find-current', new HL(ranges[next]));
    }
    const root = scrollRef.current;
    const rect = ranges[next].getBoundingClientRect();
    if (root) {
      root.scrollTo({
        top: root.scrollTop + rect.top - root.getBoundingClientRect().top - 140,
        behavior: 'smooth',
      });
    }
  };

  // 滚动联动：顶栏进度条 / 目录高亮 / 滚动后显示的小标题。
  React.useEffect(() => {
    const root = scrollRef.current;
    if (!root || loading) return;
    const onScroll = () => {
      const max = Math.max(1, root.scrollHeight - root.clientHeight);
      setProgress(Math.min(100, Math.round((root.scrollTop / max) * 100)));
      setMiniTitle(root.scrollTop > 170);
      const line = root.getBoundingClientRect().top + 140;
      let current: SectionKey = 'overview';
      for (const {key} of SECTIONS) {
        const el = sectionRefs.current[key];
        if (el && el.getBoundingClientRect().top <= line) current = key;
      }
      if (!programmaticScrollRef.current) setActive(current);
    };
    root.addEventListener('scroll', onScroll, {passive: true});
    onScroll();
    return () => root.removeEventListener('scroll', onScroll);
  }, [loading, view, summary]);

  const goTo = (key: SectionKey) => {
    const wasCollapsed = Boolean(collapsed[key]);
    setCollapsed((prev) => (prev[key] ? {...prev, [key]: false} : prev));
    setActive(key);
    lockProgrammaticScroll();
    // 若目标原本是折叠的，等它展开后再量位置；否则直接滚动。
    window.setTimeout(() => {
      const el = sectionRefs.current[key];
      const root = scrollRef.current;
      if (!el || !root) return;
      const top = el.getBoundingClientRect().top - root.getBoundingClientRect().top;
      root.scrollTo({top: root.scrollTop + top - 14, behavior: wasCollapsed ? 'auto' : 'smooth'});
    }, wasCollapsed ? 60 : 0);
  };

  const counts: Partial<Record<SectionKey, number>> = summary
    ? {
        changes: summary.changedFiles.length,
        attention: summary.attention.length,
        commands: extras.commands.length,
      }
    : {};

  return (
    <>
      {/* ── 顶栏 ─────────────────────────────────────────────── */}
      <header className="relative z-20 flex h-[54px] shrink-0 items-center gap-2.5 border-b border-border/80 bg-card/80 px-4 backdrop-blur-xl">
        <span className="hidden whitespace-nowrap text-[11px] text-faint sm:inline">
          <b className="font-semibold text-muted-foreground">{backLabel}</b> / 执行报告
        </span>
        {item ? (
          <button
            type="button"
            onClick={() => void copyText(item.id, '已复制任务 ID')}
            title="点击复制 ID"
            className="hidden max-w-[210px] items-center gap-1.5 truncate rounded-lg border border-border bg-card/80 px-2 py-1 font-mono text-[10px] text-muted-foreground transition-colors hover:border-primary hover:text-primary md:inline-flex"
          >
            <Copy className="size-3 shrink-0" />
            {item.id}
          </button>
        ) : null}

        <div
          className={cn(
            'pointer-events-none absolute left-1/2 top-1/2 max-w-[44%] -translate-x-1/2 truncate text-[12px] font-semibold transition-all duration-200',
            miniTitle ? '-translate-y-1/2 opacity-100' : '-translate-y-[20%] opacity-0',
          )}
        >
          {summary?.subject || item?.title || '执行报告'}
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          <div className="hidden items-center rounded-lg border border-border bg-card/70 p-0.5 sm:flex">
            {(
              [
                ['drawer', '抽屉'],
                ['split', '分屏'],
                ['focus', '专注'],
              ] as [ReportLayout, string][]
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => onLayoutChange(value)}
                aria-pressed={layout === value}
                title={`${label}布局`}
                className={cn(
                  'rounded-md px-2 py-1 text-[11px] font-medium transition-colors',
                  layout === value
                    ? 'bg-primary text-primary-foreground shadow-sm'
                    : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="mx-0.5 hidden h-4 w-px bg-border sm:block" />
          <IconButton label="查找 (⌘F)" active={findOpen} onClick={openFind}>
            <Search className="size-3.5" />
          </IconButton>
          <IconButton label="复制全文" onClick={() => void copyText(markdown ?? '', '已复制报告全文')}>
            <Copy className="size-3.5" />
          </IconButton>
          {downloadUrl ? (
            <a
              href={downloadUrl}
              download={downloadName}
              title="下载 .md"
              aria-label="下载 .md"
              className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <Download className="size-3.5" />
            </a>
          ) : null}
          <IconButton label="关闭报告" onClick={onClose}>
            <X className="size-3.5" />
          </IconButton>
        </div>
        <div
          className="absolute bottom-[-1px] left-0 h-[2px] rounded-full bg-gradient-to-r from-primary to-[#0e9f9c] transition-[width] duration-100"
          style={{width: `${progress}%`}}
        />
      </header>

      {/* ── 滚动内容 ─────────────────────────────────────────── */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto scroll-smooth scrollbar-thin">
        {loading ? (
          <ReportSkeleton />
        ) : error ? (
          <div className="m-5 flex items-center gap-2.5 rounded-xl border border-destructive/25 bg-destructive-muted/60 px-5 py-4 text-[13px] text-destructive">
            <AlertTriangle className="size-4 shrink-0" />
            打开报告失败：{error}
          </div>
        ) : !summary ? (
          <div className="m-5 rounded-xl border border-border bg-card px-5 py-10 text-center text-[12.5px] text-faint">
            这份工单还没有执行报告。执行一次 dry-run 或 apply 后即可查看。
          </div>
        ) : (
          <>
            <Hero
              summary={summary}
              itemKind={item?.kind}
              primary={primary}
              downloadUrl={downloadUrl}
              onPrimary={() => setConfirmOpen(true)}
              onCopy={() => void copyText(markdown ?? '', '已复制报告全文')}
            />

            <div className="grid grid-cols-1 gap-5 px-4 pb-12 pt-2 lg:grid-cols-[172px_minmax(0,1fr)] lg:px-5">
              <ReportToc
                items={SECTIONS.map((section) => ({
                  ...section,
                  count: counts[section.key],
                }))}
                active={active}
                progress={progress}
                onGo={goTo}
              />

              <main ref={bodyRef} className="min-w-0">
                <ViewBar view={view} onView={setView} onFind={openFind} />

                {view === 'raw' ? (
                  <RawView
                    markdown={markdown ?? ''}
                    onCopy={() => void copyText(markdown ?? '', '已复制原始 Markdown')}
                  />
                ) : (
                  <div className="space-y-3.5">
                    <WorkbenchSection
                      sectionKey="overview"
                      title="概览"
                      sub="需求与关键指标"
                      collapsed={Boolean(collapsed.overview)}
                      onToggle={() => toggleSection(setCollapsed, 'overview')}
                      register={(el) => (sectionRefs.current.overview = el)}
                    >
                      <OverviewBody summary={summary} descriptionHtml={descriptionHtml} />
                    </WorkbenchSection>

                    <WorkbenchSection
                      sectionKey="attention"
                      title="风险提示"
                      sub={summary.attention.length ? `${summary.attention.length} 项需要关注` : '本次执行无异常'}
                      collapsed={Boolean(collapsed.attention)}
                      onToggle={() => toggleSection(setCollapsed, 'attention')}
                      register={(el) => (sectionRefs.current.attention = el)}
                    >
                      <AttentionBody summary={summary} onGo={goTo} />
                    </WorkbenchSection>

                    <WorkbenchSection
                      sectionKey="changes"
                      title="改动文件"
                      sub={`${summary.changedFiles.length} 个 · 按模块分组`}
                      collapsed={Boolean(collapsed.changes)}
                      onToggle={() => toggleSection(setCollapsed, 'changes')}
                      register={(el) => (sectionRefs.current.changes = el)}
                    >
                      <ChangesBody
                        summary={summary}
                        reasons={extras.reasons}
                        filter={fileFilter}
                        onFilter={setFileFilter}
                        openFiles={openFiles}
                        onToggleFile={(key) => setOpenFiles((prev) => ({...prev, [key]: !prev[key]}))}
                      />
                    </WorkbenchSection>

                    <WorkbenchSection
                      sectionKey="sync"
                      title="同步结果"
                      sub={summary.sync ? '自动同步到主仓库' : '未同步'}
                      collapsed={Boolean(collapsed.sync)}
                      onToggle={() => toggleSection(setCollapsed, 'sync')}
                      register={(el) => (sectionRefs.current.sync = el)}
                    >
                      <SyncBody summary={summary} written={extras.written} />
                    </WorkbenchSection>

                    <WorkbenchSection
                      sectionKey="output"
                      title="执行输出"
                      sub="最终结论与完整输出"
                      collapsed={Boolean(collapsed.output)}
                      onToggle={() => toggleSection(setCollapsed, 'output')}
                      register={(el) => (sectionRefs.current.output = el)}
                    >
                      <OutputBody summary={summary} appendices={extras.output} expanded={expandedCode} onToggleCode={(key) => setExpandedCode((prev) => ({...prev, [key]: !prev[key]}))} />
                    </WorkbenchSection>

                    <WorkbenchSection
                      sectionKey="commands"
                      title="命令与提示词"
                      sub={`${extras.commands.length} 段`}
                      collapsed={Boolean(collapsed.commands)}
                      onToggle={() => toggleSection(setCollapsed, 'commands')}
                      register={(el) => (sectionRefs.current.commands = el)}
                    >
                      <CommandsBody
                        appendices={extras.commands}
                        expanded={expandedCode}
                        onToggleCode={(key) => setExpandedCode((prev) => ({...prev, [key]: !prev[key]}))}
                      />
                    </WorkbenchSection>

                    <WorkbenchSection
                      sectionKey="meta"
                      title="工单元信息"
                      sub="点击任意值复制"
                      collapsed={Boolean(collapsed.meta)}
                      onToggle={() => toggleSection(setCollapsed, 'meta')}
                      register={(el) => (sectionRefs.current.meta = el)}
                    >
                      <MetaBody summary={summary} itemExecutor={item?.executor} />
                    </WorkbenchSection>
                  </div>
                )}
              </main>
            </div>
          </>
        )}
      </div>

      {findOpen ? (
        <div className="fixed left-1/2 top-5 z-50 flex w-[min(460px,92vw)] -translate-x-1/2 items-center gap-2 rounded-xl border border-border bg-card/95 px-3 py-2 shadow-[0_20px_60px_-18px_rgba(40,38,100,0.28)] backdrop-blur">
          <Search className="size-3.5 shrink-0 text-faint" />
          <input
            ref={findInputRef}
            value={findQuery}
            onChange={(event) => setFindQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                jumpMatch(event.shiftKey ? -1 : 1);
              }
            }}
            placeholder="在报告中查找…"
            className="min-w-0 flex-1 bg-transparent text-[12px] text-foreground outline-none placeholder:text-faint"
          />
          <span className="min-w-[48px] shrink-0 text-center text-[11px] tabular-nums text-faint">
            {findQuery ? (matchIndex < 0 ? '无结果' : `${matchIndex + 1}/${findRangesRef.current.length}`) : ''}
          </span>
          <FindButton label="上一个" onClick={() => jumpMatch(-1)} disabled={matchIndex < 0}>
            <ChevronUp className="size-3.5" />
          </FindButton>
          <FindButton label="下一个" onClick={() => jumpMatch(1)} disabled={matchIndex < 0}>
            <ChevronDown className="size-3.5" />
          </FindButton>
          <FindButton label="关闭查找" onClick={closeFind}>
            <X className="size-3.5" />
          </FindButton>
        </div>
      ) : null}

      {confirmOpen && primary ? (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-[rgba(28,28,52,0.4)] backdrop-blur-sm"
          onClick={() => setConfirmOpen(false)}
        >
          <div
            className="w-[min(400px,90vw)] rounded-2xl bg-card p-6 shadow-[0_30px_80px_rgba(20,20,50,0.3)]"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3.5 flex size-10 items-center justify-center rounded-xl bg-primary-muted text-primary">
              <AlertTriangle className="size-5" />
            </div>
            <h3 className="text-[15px] font-semibold">{primary.confirm}</h3>
            <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">{primary.desc}</p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmOpen(false)}
                className="rounded-lg border border-border bg-card px-3.5 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-secondary"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirmOpen(false);
                  void runItemAction(primary.action, item!.id);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-1.5 text-[12px] font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
              >
                {primary.action === 'apply' ? <Play className="size-3.5" /> : <RotateCw className="size-3.5" />}
                {primary.label}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

function toggleSection(
  setter: React.Dispatch<React.SetStateAction<Partial<Record<SectionKey, boolean>>>>,
  key: SectionKey,
) {
  setter((prev) => ({...prev, [key]: !prev[key]}));
}

function IconButton({
  label,
  active,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex size-7 items-center justify-center rounded-md transition-colors',
        active ? 'bg-primary-muted text-primary' : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
      )}
    >
      {children}
    </button>
  );
}

function FindButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className="inline-flex size-6 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-primary-muted hover:text-primary disabled:opacity-40"
    >
      {children}
    </button>
  );
}

/* ── Hero ──────────────────────────────────────────────────── */

type Primary = {action: 'apply' | 'exec'; label: string; confirm: string; desc: string} | null;

function Hero({
  summary,
  itemKind,
  primary,
  downloadUrl,
  onPrimary,
  onCopy,
}: {
  summary: NonNullable<ReturnType<typeof useApp>['reportView']['summary']>;
  itemKind?: Kind;
  primary: Primary;
  downloadUrl: string | null;
  onPrimary: () => void;
  onCopy: () => void;
}) {
  const tone = summary.outcome === 'failure' ? 'bad' : summary.dryRun ? 'info' : 'ok';
  const Icon: LucideIcon = tone === 'bad' ? AlertTriangle : tone === 'info' ? Info : CircleCheck;
  const verdictTitle =
    summary.outcome === 'success'
      ? summary.dryRun ? '试运行通过' : '执行成功'
      : summary.outcome === 'failure'
        ? '执行未通过'
        : '尚未执行';
  const metrics = [
    {label: '耗时', value: summary.duration ?? '—', suffix: ''},
    {label: '改动文件', value: `${summary.changedFiles.length}`, suffix: '个'},
    {label: '已同步', value: `${summary.syncedCount}`, suffix: '个'},
    {label: '冲突', value: `${summary.conflicted.length}`, suffix: '个'},
  ];

  return (
    <section className="relative mx-4 mt-5 overflow-hidden rounded-2xl border border-white/90 bg-[linear-gradient(135deg,rgba(255,255,255,0.97),rgba(247,246,255,0.9))] px-5 py-[22px] shadow-[0_10px_34px_-14px_rgba(60,52,150,0.25)] lg:mx-[22px] lg:px-6">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(520px 220px at 88% -30%,rgba(91,84,196,.16),transparent 70%),radial-gradient(380px 200px at 10% 120%,rgba(14,159,156,.1),transparent 70%)',
        }}
      />
      <div className="relative">
        <div className="flex flex-wrap items-center gap-3">
          <div
            className={cn(
              'flex size-7 shrink-0 items-center justify-center rounded-lg text-white shadow-sm',
              tone === 'bad'
                ? 'bg-gradient-to-br from-[#f87171] to-[#dc2626]'
                : tone === 'info'
                  ? 'bg-gradient-to-br from-[#38bdf8] to-[#2563eb]'
                  : 'bg-gradient-to-br from-[#22c55e] to-[#16a34a]',
            )}
          >
            <Icon className="size-4" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-[18px] font-bold leading-tight tracking-[-0.02em] text-[#22243a]">{verdictTitle}</h1>
          </div>
          <div className="flex flex-wrap justify-end gap-1.5">
            <Badge tone="brand">{kindLabel[itemKind ?? 'task'] ?? (summary.meta.kind || '任务')}</Badge>
            <Badge tone={tone}>{statusLabel[summary.meta.status] ?? (summary.meta.status || '—')}</Badge>
            <Badge tone="teal">{summary.dryRun ? 'dry-run 试运行' : 'apply 已落地'}</Badge>
            <Badge>{summary.meta.model || '默认模型'}</Badge>
          </div>
        </div>

        <div className="mt-4 text-[14px] font-semibold tracking-[-0.01em] text-[#22243a]">
          {summary.subject || '执行报告'}
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {metrics.map((metric) => (
            <div key={metric.label} className="rounded-xl border border-[rgba(38,42,80,0.09)] bg-white/75 px-3 py-2">
              <div className="text-[10.5px] text-[#8b8fa8]">{metric.label}</div>
              <div className="mt-0.5 text-[18px] font-bold tabular-nums tracking-[-0.03em] text-[#22243a]">
                {metric.value}
                {metric.suffix ? <small className="ml-0.5 text-[11px] font-semibold text-[#8b8fa8]">{metric.suffix}</small> : null}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {primary ? (
            <button
              type="button"
              onClick={onPrimary}
              className="inline-flex h-8 items-center gap-1.5 rounded-[10px] bg-gradient-to-br from-primary to-[#7c6ee6] px-3.5 text-[12px] font-semibold text-white shadow-[0_6px_18px_rgba(91,84,196,0.3)] transition-all hover:-translate-y-px hover:shadow-[0_10px_24px_rgba(91,84,196,0.42)]"
            >
              {primary.action === 'apply' ? <Play className="size-3.5" /> : <RotateCw className="size-3.5" />}
              {primary.label}
            </button>
          ) : null}
          <button
            type="button"
            onClick={onCopy}
            className="inline-flex h-8 items-center gap-1.5 rounded-[10px] border border-border bg-white px-3.5 text-[12px] font-semibold text-[#5b5f78] transition-all hover:-translate-y-px hover:shadow-md"
          >
            <Copy className="size-3.5" />
            复制报告
          </button>
          {downloadUrl ? (
            <a
              href={downloadUrl}
              download
              className="inline-flex h-8 items-center gap-1.5 rounded-[10px] border border-border bg-white/60 px-3.5 text-[12px] font-semibold text-[#5b5f78] transition-all hover:-translate-y-px hover:shadow-md"
            >
              <Download className="size-3.5" />
              下载 .md
            </a>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function Badge({children, tone}: {children: React.ReactNode; tone?: string}) {
  const styles: Record<string, string> = {
    brand: 'bg-primary-muted text-primary border-primary/20',
    ok: 'bg-[#e8f7ed] text-[#16a34a] border-[#16a34a]/20',
    bad: 'bg-[#fdecec] text-[#dc2626] border-[#dc2626]/20',
    info: 'bg-[#eaf1ff] text-[#2563eb] border-[#2563eb]/20',
    teal: 'bg-[#e4f7f5] text-[#0e9f9c] border-[#0e9f9c]/20',
  };
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border border-border bg-white/80 px-2.5 py-1 text-[10.5px] font-semibold text-[#5b5f78]',
        tone ? styles[tone] : '',
      )}
    >
      {children}
    </span>
  );
}

/* ── 目录与视图切换 ──────────────────────────────────────────── */

function ReportToc({
  items,
  active,
  progress,
  onGo,
}: {
  items: {key: SectionKey; label: string; count?: number}[];
  active: SectionKey;
  progress: number;
  onGo: (key: SectionKey) => void;
}) {
  return (
    <aside className="hidden lg:block">
      <div className="sticky top-3 rounded-2xl border border-border bg-card px-3 py-3 shadow-xs">
        <div className="mb-2.5 px-2 text-[10px] font-bold uppercase tracking-[0.12em] text-faint">报告目录</div>
        <nav className="space-y-0.5">
          {items.map((entry) => {
            const on = active === entry.key;
            return (
              <button
                key={entry.key}
                type="button"
                onClick={() => onGo(entry.key)}
                className={cn(
                  'flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[11.5px] transition-colors',
                  on ? 'bg-primary-muted font-semibold text-primary' : 'text-muted-foreground hover:bg-secondary hover:text-primary',
                )}
              >
                <span className={cn('h-3.5 w-[3px] shrink-0 rounded-full', on ? 'bg-primary' : 'bg-transparent')} />
                <span className="truncate">{entry.label}</span>
                {entry.count ? (
                  <span className={cn('ml-auto rounded px-1.5 text-[10px] tabular-nums', on ? 'bg-primary/15 text-primary' : 'bg-secondary text-faint')}>
                    {entry.count}
                  </span>
                ) : null}
              </button>
            );
          })}
        </nav>
        <div className="mt-3 border-t border-border px-2 pt-2.5 text-[10px] leading-[1.6] text-faint">
          点击目录跳转 · 滚动自动高亮
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-secondary">
            <div className="h-full rounded-full bg-gradient-to-r from-primary to-[#0e9f9c] transition-[width] duration-150" style={{width: `${progress}%`}} />
          </div>
        </div>
      </div>
    </aside>
  );
}

function ViewBar({
  view,
  onView,
  onFind,
}: {
  view: 'structured' | 'raw';
  onView: (view: 'structured' | 'raw') => void;
  onFind: () => void;
}) {
  return (
    <div className="sticky top-0 z-10 mb-3 flex items-center gap-2 bg-gradient-to-b from-background via-background to-transparent py-2">
      <div className="flex gap-0.5 rounded-lg border border-border bg-card/80 p-0.5">
        {(
          [
            ['structured', '结构化'],
            ['raw', '原始 Markdown'],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => onView(value)}
            className={cn(
              'rounded-md px-2.5 py-1 text-[11.5px] font-semibold transition-colors',
              view === value ? 'bg-card text-primary shadow-sm' : 'text-faint hover:text-primary',
            )}
          >
            {label}
          </button>
        ))}
      </div>
      <span className="hidden text-[10.5px] text-faint sm:inline">按 <kbd className="rounded border border-border bg-card px-1 font-mono text-[10px]">⌘F</kbd> 查找</span>
      <button
        type="button"
        onClick={onFind}
        className="ml-auto inline-flex items-center gap-1 rounded-lg border border-border bg-card px-2.5 py-1 text-[11px] font-semibold text-muted-foreground transition-colors hover:text-primary"
      >
        <Search className="size-3" />
        查找
      </button>
    </div>
  );
}

function RawView({markdown, onCopy}: {markdown: string; onCopy: () => void}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10.5px] text-faint">完整原文 · 可复制 / 下载</span>
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex items-center gap-1 rounded-lg border border-border bg-secondary/40 px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-secondary"
        >
          <Copy className="size-3" />
          复制全文
        </button>
      </div>
      <pre className="report-raw-md">{markdown || '（报告正文为空）'}</pre>
    </div>
  );
}

/* ── 章节外壳 ───────────────────────────────────────────────── */

function WorkbenchSection({
  sectionKey,
  title,
  sub,
  collapsed,
  onToggle,
  register,
  children,
}: {
  sectionKey: SectionKey;
  title: string;
  sub?: string;
  collapsed: boolean;
  onToggle: () => void;
  register: (el: HTMLElement | null) => void;
  children: React.ReactNode;
}) {
  const num = SECTIONS.findIndex((entry) => entry.key === sectionKey) + 1;
  return (
    <section
      ref={register}
      id={`sec-${sectionKey}`}
      className="scroll-mt-16 overflow-hidden rounded-2xl border border-border bg-card shadow-[0_2px_10px_rgba(40,38,100,0.04)]"
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={!collapsed}
        className="flex w-full items-center gap-2.5 px-4 py-3 text-left transition-colors hover:bg-primary-muted/40"
      >
        <span className="flex size-[22px] shrink-0 items-center justify-center rounded-lg bg-primary-muted text-[10.5px] font-bold text-primary">
          {String(num).padStart(2, '0')}
        </span>
        <h2 className="text-[13px] font-bold tracking-[-0.01em] text-foreground">{title}</h2>
        {sub ? <span className="text-[10.5px] font-normal text-faint">{sub}</span> : null}
        <ChevronDown className={cn('ml-auto size-4 shrink-0 text-faint transition-transform', collapsed && '-rotate-90')} />
      </button>
      {collapsed ? null : (
        <div className="border-t border-border/60 px-4 pb-4 pt-3">{children}</div>
      )}
    </section>
  );
}

function StatsRow({items}: {items: {label: string; value: string}[]}) {
  return (
    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="rounded-xl border border-border/70 bg-[#fafbff] px-3 py-2">
          <div className="text-[10.5px] text-faint">{item.label}</div>
          <div className="mt-0.5 text-[16px] font-bold tabular-nums text-[#22243a]">{item.value}</div>
        </div>
      ))}
    </div>
  );
}

function EmptyState({icon: Icon, children}: {icon: LucideIcon; children: React.ReactNode}) {
  return (
    <div className="px-4 py-7 text-center text-[12px] text-faint">
      <div className="mx-auto mb-2.5 flex size-9 items-center justify-center rounded-xl bg-secondary text-faint">
        <Icon className="size-4" />
      </div>
      {children}
    </div>
  );
}

/* ── 各章节内容 ─────────────────────────────────────────────── */

type Summary = NonNullable<ReturnType<typeof useApp>['reportView']['summary']>;

function OverviewBody({summary, descriptionHtml}: {summary: Summary; descriptionHtml: string}) {
  const fields = summary.metaFields;
  return (
    <>
      {descriptionHtml ? (
        <div className="report-body rounded-xl border border-border/60 bg-[#fafbff] px-3.5 py-3 text-[12px] leading-[1.8] text-[#5b5f78]">
          <div dangerouslySetInnerHTML={{__html: descriptionHtml}} />
        </div>
      ) : (
        <p className="text-[12px] text-faint">这份工单没有填写需求描述。</p>
      )}
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {fields.length ? (
          fields.map((field) => (
            <div key={field.label} className="flex items-start gap-2 rounded-lg border border-border/60 bg-[#fcfcff] px-3 py-2 text-[11.5px]">
              <span className="min-w-[56px] shrink-0 text-faint">{field.label}</span>
              {field.url ? (
                <a href={field.url} target="_blank" rel="noreferrer" className="inline-flex min-w-0 items-center gap-1 break-all font-semibold text-primary hover:underline">
                  <span className="truncate">{field.value}</span>
                  <ExternalLink className="size-3 shrink-0" />
                </a>
              ) : (
                <span className="min-w-0 break-all font-semibold text-foreground">{field.value}</span>
              )}
            </div>
          ))
        ) : (
          <div className="text-[11.5px] text-faint">来源：{summary.meta.source || '手动创建'}</div>
        )}
      </div>
    </>
  );
}

function AttentionBody({summary, onGo}: {summary: Summary; onGo: (key: SectionKey) => void}) {
  if (!summary.attention.length) {
    return (
      <div className="flex items-start gap-2.5 rounded-xl border border-[#16a34a]/20 bg-[#e8f7ed] px-3.5 py-3 text-[12px] text-[#146c34]">
        <CircleCheck className="mt-0.5 size-4 shrink-0 text-[#16a34a]" />
        <div>
          <b className="block text-[12.5px]">未发现风险</b>
          全部文件已同步到主仓库，无冲突、无执行错误。
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-2.5">
      {summary.attention.map((entry) => {
        const Icon: LucideIcon = entry.tone === 'danger' ? AlertTriangle : entry.tone === 'success' ? CircleCheck : Info;
        return (
          <div
            key={`${entry.kind}-${entry.title}`}
            className={cn(
              'flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-[12px] leading-[1.7]',
              entry.tone === 'danger'
                ? 'border-[#dc2626]/20 bg-[#fdecec] text-[#a41c1c]'
                : entry.tone === 'warning'
                  ? 'border-[#d97706]/20 bg-[#fff3e0] text-[#875000]'
                  : entry.tone === 'success'
                    ? 'border-[#16a34a]/20 bg-[#e8f7ed] text-[#146c34]'
                    : 'border-[#2563eb]/20 bg-[#eaf1ff] text-[#1c4fb0]',
            )}
          >
            <Icon className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <b className="block text-[12.5px]">{entry.title}</b>
              {entry.detail ? <span className="block">{entry.detail}</span> : null}
              {entry.items?.length ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {entry.items.slice(0, 6).map((file) => (
                    <code key={file} className="rounded-md border border-border bg-white/70 px-1.5 py-0.5 font-mono text-[10.5px]">
                      {file}
                    </code>
                  ))}
                  {entry.more ? <span className="self-center text-[10.5px]">等 {entry.more} 个</span> : null}
                </div>
              ) : null}
              {entry.kind === 'files' ? (
                <button
                  type="button"
                  onClick={() => onGo('changes')}
                  className="mt-2 inline-flex items-center gap-1 text-[11.5px] font-semibold underline underline-offset-2"
                >
                  定位到改动文件 <ChevronRight className="size-3" />
                </button>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ChangesBody({
  summary,
  reasons,
  filter,
  onFilter,
  openFiles,
  onToggleFile,
}: {
  summary: Summary;
  reasons: Record<string, string[]>;
  filter: string;
  onFilter: (value: string) => void;
  openFiles: Record<string, boolean>;
  onToggleFile: (key: string) => void;
}) {
  if (!summary.changedFiles.length) {
    return <EmptyState icon={FileText}>本次执行没有改动任何文件</EmptyState>;
  }
  const query = filter.trim().toLowerCase();
  return (
    <>
      <div className="flex items-center gap-2 rounded-lg border border-border bg-[#fcfcff] px-3 py-1.5">
        <Search className="size-3.5 shrink-0 text-faint" />
        <input
          value={filter}
          onChange={(event) => onFilter(event.target.value)}
          placeholder="筛选文件路径…"
          className="min-w-0 flex-1 bg-transparent text-[11.5px] text-foreground outline-none placeholder:text-faint"
        />
        <span className="text-[10px] text-faint">{summary.changedFiles.length} 个</span>
      </div>
      <div className="mt-3 space-y-3.5">
        {summary.modules.map((group) => {
          const files = group.files.filter((file) => !query || file.toLowerCase().includes(query));
          if (!files.length) return null;
          return (
            <div key={group.name}>
              <div className="mb-2 flex items-center gap-2 text-[11px] font-bold text-muted-foreground">
                <span className="rounded-md bg-primary-muted px-2 py-0.5 font-mono text-[10px] font-semibold text-primary">{group.name}</span>
                <span className="font-normal">{group.count} 个文件</span>
                <span className="ml-auto text-[10px] font-normal text-faint">点击查看改动原因</span>
              </div>
              {files.map((file) => {
                const key = `${group.name}/${file}`;
                const open = Boolean(openFiles[key]);
                const detail = reasons[file] ?? ['本次执行修改了该文件，具体改动见执行输出摘要。'];
                return (
                  <div key={key} className="mb-1.5 overflow-hidden rounded-xl border border-border bg-white transition-colors hover:border-primary/40">
                    <div className="flex items-center">
                      <button
                        type="button"
                        onClick={() => onToggleFile(key)}
                        className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2 text-left"
                      >
                        <span className="flex size-[26px] shrink-0 items-center justify-center rounded-lg bg-secondary text-faint">
                          <FileText className="size-3.5" />
                        </span>
                        <span className="min-w-0 flex-1 break-all font-mono text-[11px] font-semibold text-foreground">{file}</span>
                        <span className="shrink-0 rounded-md bg-[#e8f7ed] px-1.5 py-0.5 text-[10px] font-semibold text-[#16a34a]">已同步</span>
                        <ChevronRight className={cn('size-3.5 shrink-0 text-faint transition-transform', open && 'rotate-90')} />
                      </button>
                      <button
                        type="button"
                        onClick={() => void copyText(file, '已复制文件路径')}
                        aria-label="复制路径"
                        title="复制路径"
                        className="mr-2 rounded p-1 text-faint transition-colors hover:bg-primary-muted hover:text-primary"
                      >
                        <Copy className="size-3" />
                      </button>
                    </div>
                    {open ? (
                      <div className="border-t border-border/60 py-2.5 pl-12 pr-4 text-[11.5px] leading-[1.8] text-muted-foreground">
                        <b className="text-foreground">改动原因</b>
                        <ul className="mt-1 list-disc pl-4">
                          {detail.map((line) => (
                            <li key={line}>{line}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </>
  );
}

function SyncBody({summary, written}: {summary: Summary; written: string[]}) {
  if (!summary.sync) {
    return <EmptyState icon={Layers}>本次执行未同步到主仓库</EmptyState>;
  }
  const files = written.length ? written : summary.changedFiles;
  return (
    <>
      <StatsRow
        items={[
          {label: '已同步', value: `${summary.syncedCount}`},
          {label: '已删除', value: `${summary.sync.deleted.length}`},
          {label: '冲突跳过', value: `${summary.conflicted.length}`},
          {label: '备份', value: summary.sync.backupDir ? '1' : '0'},
        ]}
      />
      <div className="mt-3.5 text-[11.5px] font-bold text-muted-foreground">已写入工作区</div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {files.length ? (
          files.map((file) => (
            <button
              key={file}
              type="button"
              onClick={() => void copyText(file, '已复制文件路径')}
              title="点击复制"
              className="rounded-lg border border-border bg-[#f5f6fc] px-2 py-1 font-mono text-[10.5px] text-muted-foreground transition-colors hover:border-primary hover:bg-primary-muted hover:text-primary"
            >
              {file}
            </button>
          ))
        ) : (
          <span className="text-[11px] text-faint">无</span>
        )}
      </div>
      {summary.sync.backupDir ? (
        <button
          type="button"
          onClick={() => void copyText(summary.sync!.backupDir!, '已复制备份目录')}
          title="点击复制"
          className="mt-3 flex w-full items-start gap-2 rounded-lg border border-border/60 bg-[#fcfcff] px-3 py-2 text-left text-[11.5px]"
        >
          <span className="shrink-0 text-faint">备份目录</span>
          <span className="min-w-0 break-all font-mono text-[10.5px] text-foreground/80">{summary.sync.backupDir}</span>
        </button>
      ) : null}
      {summary.sync.error ? (
        <p className="mt-2 text-[11.5px] text-destructive">同步错误：{summary.sync.error}</p>
      ) : null}
    </>
  );
}

function OutputBody({
  summary,
  appendices,
  expanded,
  onToggleCode,
}: {
  summary: Summary;
  appendices: ReportAppendix[];
  expanded: Record<string, boolean>;
  onToggleCode: (key: string) => void;
}) {
  const tone = summary.outcome === 'failure' ? 'danger' : summary.dryRun ? 'info' : 'success';
  return (
    <>
      <div
        className={cn(
          'flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-[12px] leading-[1.7]',
          tone === 'danger'
            ? 'border-[#dc2626]/20 bg-[#fdecec] text-[#a41c1c]'
            : tone === 'info'
              ? 'border-[#2563eb]/20 bg-[#eaf1ff] text-[#1c4fb0]'
              : 'border-[#16a34a]/20 bg-[#e8f7ed] text-[#146c34]',
        )}
      >
        {tone === 'danger' ? <AlertTriangle className="mt-0.5 size-4 shrink-0" /> : <Info className="mt-0.5 size-4 shrink-0" />}
        <div>
          <b className="block text-[12.5px]">{summary.verdict}</b>
          {summary.attention[0]?.detail ?? (summary.diagnostics.length ? summary.diagnostics.join(' · ') : '执行器返回的最终结论如下。')}
        </div>
      </div>
      <div className="mt-3.5 text-[11.5px] font-bold text-muted-foreground">验证结果</div>
      <div className="mt-1">
        {(
          [
            ['执行结果', summary.outcome === 'success' ? '通过' : summary.outcome === 'failure' ? '失败' : '未执行', summary.outcome === 'success' ? 'ok' : summary.outcome === 'failure' ? 'bad' : '—'],
            ['改动写入', summary.changedFiles.length ? `${summary.changedFiles.length} 个文件` : '未执行', summary.changedFiles.length ? 'ok' : '—'],
            ['同步主仓库', summary.syncedCount ? `${summary.syncedCount} 个文件` : summary.dryRun ? '未执行（试运行）' : '未同步', summary.syncedCount ? 'ok' : '—'],
          ] as [string, string, string][]
        ).map(([label, value, flag]) => (
          <div key={label} className="flex items-center gap-2 border-b border-border/60 py-1.5 text-[11.5px] last:border-0">
            {flag === 'ok' ? <CircleCheck className="size-3.5 text-[#16a34a]" /> : flag === 'bad' ? <AlertTriangle className="size-3.5 text-[#dc2626]" /> : <Info className="size-3.5 text-faint" />}
            <span className="flex-1 text-muted-foreground">{label}</span>
            <span className={cn('text-[10.5px] font-semibold', flag === 'ok' ? 'text-[#16a34a]' : flag === 'bad' ? 'text-[#dc2626]' : 'text-faint')}>{value}</span>
          </div>
        ))}
      </div>
      <div className="mt-4 text-[11.5px] font-bold text-muted-foreground">完整输出（可折叠 / 复制）</div>
      {appendices.length ? (
        appendices.map((appendix, index) => (
          <CodeBlock
            key={`${appendix.title}-${index}`}
            blockKey={`out-${index}`}
            title={appendix.title}
            lang={appendix.lang}
            text={appendix.body}
            expanded={Boolean(expanded[`out-${index}`])}
            onToggle={onToggleCode}
          />
        ))
      ) : (
        <EmptyState icon={FileText}>执行器没有返回输出摘要</EmptyState>
      )}
    </>
  );
}

function CommandsBody({
  appendices,
  expanded,
  onToggleCode,
}: {
  appendices: ReturnType<typeof extractReportExtras>['commands'];
  expanded: Record<string, boolean>;
  onToggleCode: (key: string) => void;
}) {
  if (!appendices.length) return <EmptyState icon={FileText}>本次执行没有记录命令或提示词</EmptyState>;
  return (
    <>
      <p className="mb-1 text-[11.5px] leading-[1.7] text-muted-foreground">
        命令已做提示词脱敏；点击右上角可复制完整内容。长内容默认折叠，展开后可全文查阅。
      </p>
      {appendices.map((appendix, index) => (
        <CodeBlock
          key={`${appendix.title}-${index}`}
          blockKey={`cmd-${index}`}
          title={appendix.title}
          lang={appendix.lang}
          text={appendix.body}
          expanded={Boolean(expanded[`cmd-${index}`])}
          onToggle={onToggleCode}
        />
      ))}
    </>
  );
}

function CodeBlock({
  blockKey,
  title,
  lang,
  text,
  expanded,
  onToggle,
}: {
  blockKey: string;
  title: string;
  lang: string;
  text: string;
  expanded: boolean;
  onToggle: (key: string) => void;
}) {
  const long = text.length > 430;
  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-border bg-[#fbfbfe]">
      <div className="flex items-center gap-2 border-b border-border/60 bg-[#f5f6fb] px-3 py-2">
        <span className="size-[7px] shrink-0 rounded-full bg-[#d7d9e8]" />
        <span className="truncate text-[11px] font-semibold text-foreground/80">{title}</span>
        <span className="rounded border border-border bg-card px-1.5 py-0.5 font-mono text-[9.5px] text-faint">{lang}</span>
        <span className="hidden text-[10px] text-faint sm:inline">{text.split('\n').length} 行</span>
        <div className="ml-auto flex shrink-0 gap-1">
          <button
            type="button"
            onClick={() => void copyText(text, '已复制代码')}
            className="rounded px-1.5 py-0.5 text-[10.5px] text-faint transition-colors hover:bg-card hover:text-primary"
          >
            复制
          </button>
          {long ? (
            <button
              type="button"
              onClick={() => onToggle(blockKey)}
              className="rounded px-1.5 py-0.5 text-[10.5px] text-faint transition-colors hover:bg-card hover:text-primary"
            >
              {expanded ? '收起' : '展开全部'}
            </button>
          ) : null}
        </div>
      </div>
      <div className={cn('relative overflow-hidden transition-[max-height] duration-300', expanded ? 'max-h-none' : 'max-h-[210px]')}>
        <pre className="whitespace-pre-wrap break-all px-4 py-3 font-mono text-[10.8px] leading-[1.75] text-[#3c3f5c]">{text}</pre>
        {!expanded && long ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-[52px] bg-gradient-to-t from-[#fbfbfe] to-transparent" />
        ) : null}
      </div>
    </div>
  );
}

function MetaBody({summary, itemExecutor}: {summary: Summary; itemExecutor?: string}) {
  const meta = summary.meta;
  const rows: [string, string, boolean?][] = [
    ['任务 ID', meta.id, true],
    ['状态', statusLabel[meta.status] ?? meta.status],
    ['类型', kindLabel[(meta.kind || 'task') as Kind] ?? meta.kind],
    ['执行模式', meta.mode],
    ['模型', meta.model, true],
    ['执行端', itemExecutor || 'codebuddy CLI'],
    ['来源', meta.source, true],
    ['映射仓库', meta.repo, true],
    ['worktree', meta.worktree, true],
  ];
  return (
    <>
      <div className="grid gap-2 sm:grid-cols-2">
        {rows.map(([label, value, mono]) => (
          <button
            key={label}
            type="button"
            onClick={() => void copyText(value || '—', `已复制${label}`)}
            title="点击复制"
            className="flex min-w-0 items-start gap-2 rounded-lg border border-border/60 bg-[#fcfcff] px-3 py-2 text-left text-[11.5px] transition-colors hover:border-primary hover:bg-primary-muted/40"
          >
            <span className="min-w-[56px] shrink-0 text-faint">{label}</span>
            <span className={cn('min-w-0 break-all font-semibold text-foreground', mono && 'font-mono text-[10.5px] font-normal')}>
              {value || '—'}
            </span>
          </button>
        ))}
      </div>
      <p className="mt-3 text-[10.5px] leading-[1.6] text-faint">所有值点击即可复制 · 查询更多追溯信息请下载完整 .md</p>
    </>
  );
}

function ReportSkeleton() {
  return (
    <div className="mx-4 mt-4 lg:mx-5">
      <div className="mb-4 h-[220px] animate-pulse rounded-2xl border border-border bg-secondary/40" />
      <div className="mb-4 h-[80px] animate-pulse rounded-xl border border-border bg-secondary/30" />
      <div className="h-[320px] animate-pulse rounded-2xl border border-border bg-secondary/40" />
      <div className="mt-4 flex items-center justify-center gap-2 text-[12px] text-faint">
        <Loader2 className="size-4 animate-spin" />
        正在载入报告…
      </div>
    </div>
  );
}
