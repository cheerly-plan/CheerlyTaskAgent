/**
 * 报告 → 可视化摘要模型。
 *
 * 后端 `report.py` 生成的 Markdown 是完整的人类可读记录：关键结论（成功与否、
 * 耗时、改了哪些文件）埋在三张「字段 / 值」表和几百行原始输出之间。这里把
 * 前两张表（基本信息 / 执行结果）解析成结构化事实，并提炼出给人看的重点：
 *
 * - 三态结论与一句话 verdict（成功 · 4 个文件已改动 / 失败 · 1 个冲突）；
 * - 指标卡（执行结果 / 耗时 / 改动文件 / 同步数）；
 * - 提示列表：错误、同步冲突、改动文件、输出截断等需要注意的信息；
 * - 改动文件的模块分布（条形图数据）；
 * - 诊断：原始输出被截断 / 没有最终结论，提示结论的可信度。
 *
 * 只认前两张表是刻意的约定，而非通用 Markdown 解析：报告里的子表（改动文件、
 * 自动同步）都挂在 `###` 或 `####` 标题下，不会被误当成主表。
 */

export type Tone = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

export interface Attention {
  kind: 'error' | 'failure' | 'conflict' | 'files' | 'warning' | 'info';
  tone: Tone;
  title: string;
  detail?: string;
  items?: string[];
  more?: number;
}

export interface Fact {
  label: string;
  value: string;
  /** ID / 路径这类值用等宽字体，方便复制。 */
  mono?: boolean;
}

export interface FactGroup {
  label: string;
  facts: Fact[];
}

export interface ModuleSlice {
  name: string;
  files: string[];
  count: number;
  /** 相对最大模块的百分比，直接做条形图宽度。 */
  percent: number;
}

export interface ReportSummary {
  /** 报告的 `#` 标题。 */
  headline: string;
  /** 去掉「工作项报告：」前缀后的工单标题。 */
  subject: string;
  outcome: 'success' | 'failure' | 'pending';
  /** 一句话结论。 */
  verdict: string;
  ok: boolean | null;
  dryRun: boolean | null;
  duration: string | null;
  changedFiles: string[];
  syncedCount: number;
  conflicted: string[];
  /** 自动同步明细（同步小节存在时才有）。 */
  sync: {
    deleted: string[];
    skipped: string[];
    backupDir: string | null;
    error: string;
  } | null;
  stats: {label: string; value: string; hint?: string; tone: Tone}[];
  attention: Attention[];
  modules: ModuleSlice[];
  groups: FactGroup[];
  /** 项目 / 模式的「一眼可辨」badge 值（来自基本信息表）。 */
  meta: {
    kind: string;
    status: string;
    mode: string;
    model: string;
    source: string;
    repo: string;
    worktree: string;
  };
  /** 已剥掉标题 / 主表 / 改动与同步清单的正文 Markdown，供「完整报告」渲染。 */
  rest: string;
  diagnostics: string[];
}

const FILE_ITEM = /^\s*[-*]\s+`([^`]+)`\s*$/;
const WARNING_LINE = /^>\s*⚠️\s*(执行错误|同步错误)[：:]?\s*(.*)$/;
const MONO_FIELDS = new Set(['ID', 'git 地址', '映射仓库', 'worktree']);

/** 去掉 inline 标记（加粗、反引号），得到纯文本。 */
function stripInline(value: string): string {
  return value.replace(/\*\*(.+?)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1').trim();
}

function isSeparatorRow(line: string): boolean {
  return /^\s*\|[\s:|-]+\|\s*$/.test(line);
}

/** `| 字段 | 值 |` 表 → 键值对；跳过表头与分隔行。 */
function parseTable(lines: string[], start: number): {rows: [string, string][]; end: number} {
  const rows: [string, string][] = [];
  let i = start;
  while (i < lines.length && /^\s*\|/.test(lines[i])) {
    const cells = lines[i]
      .split('|')
      .slice(1, -1)
      .map(stripInline);
    if (!isSeparatorRow(lines[i]) && cells.length >= 2 && cells[0] !== '字段') {
      rows.push([cells[0], cells.slice(1).join(' | ')]);
    }
    i++;
  }
  return {rows, end: i};
}

/** 取 `### 标题` 之后、下一个标题之前的反引号列表项。 */
function listUnderHeading(lines: string[], pattern: RegExp, consumed: Set<number>): string[] {
  const files: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const heading = lines[i].match(/^#{1,6}\s+(.*)$/);
    if (!heading || !pattern.test(stripInline(heading[1]))) continue;
    let hit = false;
    for (let j = i + 1; j < lines.length && !/^#{1,6}\s/.test(lines[j]); j++) {
      const match = lines[j].match(FILE_ITEM);
      if (!match) continue;
      files.push(match[1].trim());
      consumed.add(j);
      hit = true;
    }
    if (hit) consumed.add(i); // 整节都已结构化展示，正文里不再重复
  }
  return files;
}

/** 取某个 `###` 小节内第一张「字段 / 值」表（用于同步结果的数量与备份目录）。 */
function factsUnderHeading(lines: string[], pattern: RegExp): [string, string][] {
  const start = lines.findIndex((line) => {
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    return heading ? pattern.test(stripInline(heading[2])) : false;
  });
  if (start < 0) return [];
  for (let i = start + 1; i < lines.length && !/^#{1,3}\s/.test(lines[i]); i++) {
    if (/^\s*\|/.test(lines[i])) return parseTable(lines, i).rows;
  }
  return [];
}

/**
 * 「### 自动同步到主仓库」整节（含子标题）已经结构化展示到「改动」标签页，
 * 从正文中移除：包括标题、子标题、其中的「字段 / 值」表与列表。
 */
function markSyncSections(lines: string[], consumed: Set<number>) {
  let inside = false;
  for (let i = 0; i < lines.length; i++) {
    const heading = lines[i].match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      if (heading[1].length <= 3) inside = heading[1].length === 3 && /自动同步/.test(stripInline(heading[2]));
      if (inside) consumed.add(i);
      continue;
    }
    if (inside) consumed.add(i);
  }
}

/** 目录聚合：`web/src/pages/a.tsx` → `web/src/pages`；过深的路径取末两级。 */
export function moduleOf(file: string): string {
  const clean = file.replace(/\s*[（(][^）)]*[）)]\s*$/, '').trim();
  const parts = clean.split('/');
  if (parts.length <= 1) return clean || '根目录';
  const dirs = parts.slice(0, -1);
  return dirs.length >= 3 ? dirs.slice(-2).join('/') : dirs.join('/');
}

export function analyzeReport(markdown: string): ReportSummary {
  const lines = String(markdown || '').split('\n');
  const consumed = new Set<number>();

  // ① 标题 + 前两张主表（基本信息 / 执行结果）
  let headline = '';
  const groups: FactGroup[] = [];
  const facts = new Map<string, string>();

  /** 把「字段 / 值」表并入分组与字典，并把表头、分隔行一起标记为已消费。 */
  const takeTable = (label: string, start: number) => {
    const {rows, end} = parseTable(lines, start);
    if (!rows.length) return 0;
    groups.push({
      label,
      facts: rows.map(([key, value]) => ({label: key, value, mono: MONO_FIELDS.has(key)})),
    });
    rows.forEach(([key, value]) => facts.set(key, value));
    for (let j = start; j < end; j++) consumed.add(j);
    return end;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const h1 = line.match(/^#\s+(.*)$/);
    if (h1 && !headline) {
      headline = stripInline(h1[1]);
      consumed.add(i);
      continue;
    }
    // 报告开头可能紧跟一个没有 `##` 标题的「字段 / 值」表 → 作为「基本信息」。
    if (!groups.length && i > 0 && /^\s*\|/.test(line) && /^\s*\|.*\|\s*$/.test(line)) {
      const end = takeTable('基本信息', i);
      if (end) {
        i = end - 1;
        continue;
      }
    }
    if (!/^##\s+/.test(line) || groups.length >= 2) continue;
    // 标题与表格之间可能空一行（后端用 lines += [...""] 拼接）。
    const tableStart = /^\s*\|/.test(lines[i + 1] ?? '') ? i + 1 : /^\s*\|/.test(lines[i + 2] ?? '') && !(lines[i + 1] ?? '').trim() ? i + 2 : -1;
    if (tableStart < 0) continue;
    const end = takeTable(stripInline(line.replace(/^##\s+/, '')), tableStart);
    if (end) {
      for (let j = i; j < end; j++) consumed.add(j);
      i = end - 1;
    }
  }

  // ② 清单类小节：先整节标记为已消费（不重复展示），再按小节取文件。
  markSyncSections(lines, consumed);

  // 同步子表（#### 自动同步到主仓库）里的数量也算进「字段 → 值」字典。
  const syncFacts = new Map(factsUnderHeading(lines, /自动同步/));
  const factOf = (key: string) => facts.get(key) ?? syncFacts.get(key);

  const changedFiles = listUnderHeading(lines, /改动文件/, consumed);
  const conflicted = listUnderHeading(lines, /同步冲突/, consumed);
  const deleted = listUnderHeading(lines, /已删除/, consumed);
  const skipped = listUnderHeading(lines, /跳过/, consumed);
  const backupDir = factOf('备份目录') ?? null;
  const syncError =
    lines.map((line) => line.match(WARNING_LINE)).find((m) => m?.[1] === '同步错误')?.[2]?.trim() ?? '';

  // ③ 结论与警示
  const okRaw = facts.get('成功');
  const ok = okRaw === undefined ? null : okRaw === '是';
  const dryRaw = factOf('dry-run');
  const dryRun = dryRaw === undefined ? null : dryRaw === '是';
  const execError = lines.map((line) => line.match(WARNING_LINE)).find((m) => m?.[1] === '执行错误')?.[2]?.trim() ?? '';
  const truncated = /输出被截断，未捕获最终结论/.test(markdown);
  const noFinal = /执行输出摘要（无明确最终结论）/.test(markdown);

  const countOf = (value?: string) => Number(/^(\d+)/.exec(value ?? '')?.[1] ?? 0);
  const changedCount = facts.has('改动文件') ? countOf(facts.get('改动文件')) : changedFiles.length;
  const syncedCount = countOf(factOf('已同步'));
  const conflictRaw = factOf('冲突跳过');
  const conflictCount = conflictRaw === undefined ? conflicted.length : countOf(conflictRaw);

  const outcome: ReportSummary['outcome'] =
    ok === null ? 'pending' : ok && !execError ? 'success' : 'failure';

  // ④ 一句话结论
  const parts: string[] = [
    outcome === 'success'
      ? dryRun
        ? '试运行通过，未改动主仓库'
        : '执行成功'
      : outcome === 'failure'
        ? '执行未通过'
        : '尚未执行',
  ];
  if (outcome !== 'pending') parts.push(changedCount ? `${changedCount} 个文件已改动` : '无文件改动');
  if (conflictCount) parts.push(`${conflictCount} 个文件同步冲突`);
  else if (syncError) parts.push('同步出错');

  // ⑤ 提示：按严重程度排序（错误 → 冲突 → 截断 → 改动文件）
  const attention: Attention[] = [];
  if (execError) {
    attention.push({kind: 'error', tone: 'danger', title: '执行错误', detail: execError});
  }
  if (ok === false && !execError) {
    attention.push({
      kind: 'failure',
      tone: 'danger',
      title: '执行未成功',
      detail: '展开「完整报告 · 执行结果」查看后端返回的详细信息。',
    });
  }
  if (conflictCount) {
    attention.push({
      kind: 'conflict',
      tone: 'warning',
      title: `同步冲突 ${conflictCount} 个文件`,
      detail: '主仓库与本次改动分叉，出于安全未自动覆盖；请比对备份目录中的 .main / 改动版本后手动合并。',
      items: conflicted,
    });
  }
  if (syncError && !conflictCount) {
    attention.push({
      kind: 'error',
      tone: 'danger',
      title: '同步到主仓库出错',
      detail: '改动仍在 worktree 中，需要手动同步。',
    });
  }
  if (truncated || noFinal) {
    attention.push({
      kind: 'warning',
      tone: 'warning',
      title: truncated ? '执行输出被截断' : '未捕获最终结论',
      detail: truncated
        ? '执行器输出在结束前被截断，摘要可能不完整，建议下载完整 .md。'
        : '执行器没有返回明确的 final 事件，摘要来自过程记录。',
    });
  }
  // 改动文件属于「已知情况」而非风险，只在同步不完整或未落库时提示。
  if (changedFiles.length && !dryRun && syncedCount < changedFiles.length) {
    attention.push({
      kind: 'files',
      tone: 'info',
      title: syncedCount
        ? `改动 ${changedFiles.length} 个文件，仅 ${syncedCount} 个已同步`
        : `改动 ${changedFiles.length} 个文件，尚未同步到主仓库`,
      detail: syncedCount ? '其余文件未写入主仓库，需手动处理或在「改动」中核对。' : '改动只在 worktree 中。',
      items: changedFiles.slice(0, 6),
      more: Math.max(0, changedFiles.length - 6),
    });
  }
  if (outcome === 'pending') {
    attention.push({
      kind: 'info',
      tone: 'neutral',
      title: '尚未生成执行结果',
      detail: '执行 dry-run 或 apply 后，这里会显示耗时、改动文件与同步状态。',
    });
  }

  // ⑥ 模块分布
  const buckets = new Map<string, string[]>();
  for (const file of changedFiles) {
    const name = moduleOf(file);
    buckets.set(name, [...(buckets.get(name) ?? []), file]);
  }
  const largest = Math.max(1, ...[...buckets.values()].map((files) => files.length));
  const modules: ModuleSlice[] = [...buckets.entries()]
    .map(([name, files]) => ({name, files, count: files.length}))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
    .map((slice) => ({...slice, percent: Math.round((slice.count / largest) * 100)}));

  // ⑦ 指标卡
  const duration = facts.get('耗时') ?? null;
  const stats: ReportSummary['stats'] = [
    {
      label: '执行结果',
      value:
        outcome === 'success' ? (dryRun ? '试运行通过' : '成功') : outcome === 'failure' ? '失败' : '未执行',
      hint: dryRun === null ? undefined : dryRun ? 'dry-run 试运行' : 'apply 已落地',
      tone: outcome === 'success' ? 'success' : outcome === 'failure' ? 'danger' : 'neutral',
    },
    {label: '耗时', value: duration ?? '—', hint: duration ? '执行器总耗时' : undefined, tone: 'neutral'},
    {
      label: '改动文件',
      value: String(changedCount),
      hint: modules.length ? `分布在 ${modules.length} 个模块` : undefined,
      tone: changedCount ? 'info' : 'neutral',
    },
    {
      label: '同步主仓库',
      value: String(syncedCount),
      hint: conflictCount ? `${conflictCount} 个冲突待处理` : syncedCount ? '已全部写入' : undefined,
      tone: conflictCount ? 'warning' : syncedCount ? 'success' : 'neutral',
    },
  ];

  return {
    headline,
    subject: headline.replace(/^工作项报告[：:]\s*/, ''),
    outcome,
    meta: {
      kind: facts.get('类型') ?? '',
      status: facts.get('状态') ?? '',
      mode: facts.get('执行模式') ?? '',
      model: facts.get('模型') ?? '',
      source: facts.get('来源') ?? '',
      repo: facts.get('映射仓库') ?? '',
      worktree: facts.get('worktree') ?? '',
    },
    sync: backupDir || deleted.length || skipped.length || syncError
      ? {deleted, skipped, backupDir, error: syncError}
      : null,
    verdict: parts.join(' · '),
    ok,
    dryRun,
    duration,
    changedFiles,
    syncedCount,
    conflicted,
    stats,
    attention,
    modules,
    groups,
    rest: lines.filter((_, index) => !consumed.has(index)).join('\n'),
    diagnostics: [truncated ? '原始输出被截断' : '', noFinal ? '无明确最终结论' : ''].filter(Boolean),
  };
}