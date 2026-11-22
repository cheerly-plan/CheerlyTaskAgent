
const esc = (value: unknown) =>
  String(value ?? '').replace(/[&<>"']/g, (c) => {
    const map: Record<string, string> = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
    return map[c];
  });

/**
 * @param breakOnNewline 段落内换行是否输出 `<br>`。来源系统的描述是自由文本
 *   （「处理人：x⏎优先级：y」），不换行会挤成一行；报告正文按 Markdown 规范
 *   保持软换行。
 *
 * 段落里出现来源系统的 HTML（`<p>`、`<br/>`）时会转成安全的行内内容，
 * 其余标签一律剥离，不会执行。
 */
export function renderMarkdown(markdown: string, breakOnNewline = false): string {
  const lines = String(markdown || '').split('\n');
  const out: string[] = [];
  let i = 0;

  const withBreaks = (html: string) => (breakOnNewline ? html.replace(/\n/g, '<br>') : html);

  const inline = (text: string) =>
    esc(text)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  /** 行内只认报告自己的 <code>（后端用反引号转出来的），其余 HTML 一律转义。 */
  const inlineTagged = (text: string) =>
    text
      .split(/(<code>[\s\S]*?<\/code>)/g)
      .map((part) =>
        /^<code>[\s\S]*<\/code>$/.test(part)
          ? `<code>${esc(part.slice(6, -7))}</code>`
          : inline(part),
      )
      .join('');

  const fenced = (code: string[]) => {
    const full = code.join('\n');
    if (full.length > 2000) {
      const shown = full.slice(0, 6000);
      const tail = full.length > 6000 ? '\n\n…（已截断，完整内容请下载 .md）' : '';
      return `<details><summary>原始输出（${full.length} 字符，点击展开）</summary><pre class="md-code">${esc(shown)}${tail}</pre></details>`;
    }
    return `<pre class="md-code">${esc(full)}</pre>`;
  };

  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*<\/details>/i.test(line)) {
      i++;
      continue;
    }

    if (/^\s*<details\b/i.test(line)) {
      const block: string[] = [];
      let depth = 0;
      while (i < lines.length) {
        const current = lines[i];
        block.push(current);
        if (/^\s*<details\b/i.test(current)) depth += 1;
        if (/^\s*<\/details>/i.test(current)) {
          depth -= 1;
          if (depth === 0) {
            i++;
            break;
          }
        }
        i++;
      }
      let summaryText = '详情';
      for (const bl of block) {
        const match = bl.match(/<summary[^>]*>([\s\S]*?)<\/summary>/i);
        if (match) {
          summaryText = match[1];
          break;
        }
      }
      const opened = /\bopen\b/i.test(block[0]) || /提示词/.test(summaryText);
      const innerLines = block.slice(1).filter(
        (current) =>
          !/^\s*<\/details>/i.test(current) &&
          !/<summary[^>]*>/i.test(current) &&
          !/^\s*<details\b/i.test(current),
      );
      // 提示词 / 原始输出是原样下发的文本：保持代码块（保留缩进与换行）语义。
      // 「提示词」里嵌着需求描述，会带上 <p> 之类的标签，这里也走一遍清理；
      // 原始输出保持逐字不变，方便排查。
      const isPrompt = /提示词/.test(summaryText);
      const cleaned = isPrompt ? stripHtml(innerLines.join('\n')) : innerLines.join('\n');
      const inner = renderMarkdown(cleaned, breakOnNewline);
      out.push(`<details${opened ? ' open' : ''}><summary>${inline(summaryText)}</summary>${inner}</details>`);
      continue;
    }

    if (/^\s*```/.test(line)) {
      i++;
      const code: string[] = [];
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        code.push(lines[i]);
        i++;
      }
      i++;
      out.push(fenced(code));
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 1, 5);
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const cells = (row: string) => row.split('|').slice(1, -1).map((cell) => cell.trim());
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        rows.push(cells(lines[i]));
        i++;
      }
      out.push(
        '<table class="md-table"><thead><tr>' +
          head.map((cell) => `<th>${inline(cell)}</th>`).join('') +
          '</tr></thead><tbody>' +
          rows.map((row) => '<tr>' + row.map((cell) => `<td>${inline(cell)}</td>`).join('') + '</tr>').join('') +
          '</tbody></table>',
      );
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      out.push(`<blockquote>${inline(line.replace(/^\s*>\s?/, ''))}</blockquote>`);
      i++;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(inline(lines[i].replace(/^\s*[-*]\s+/, '')));
        i++;
      }
      out.push('<ul>' + items.map((item) => `<li>${item}</li>`).join('') + '</ul>');
      continue;
    }

    if (!line.trim()) {
      i++;
      continue;
    }

    // 来源系统（TAPD 等）的富文本会以裸 HTML 出现在正文里，
    // 例如「<p>可以调整成 tab</p>」，直接转义会把标签当文字显示。
    if (looksLikeHtml(line)) {
      out.push(...htmlBlock(line, withBreaks, (text) => inlineTagged(text)));
      i++;
      continue;
    }

    out.push(`<p>${withBreaks(inline(line))}</p>`);
    i++;
  }

  return out.join('\n');
}

/** 报告用到的 HTML 实体（`&nbsp;` 之类）还原成字符；未知实体原样保留。 */
const ENTITIES: Record<string, string> = {
  '&nbsp;': ' ',
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&#39;': "'",
  '&apos;': "'",
};

const decodeEntities = (text: string) =>
  text.replace(/&(nbsp|amp|lt|gt|quot|#39|apos);/g, (match) => ENTITIES[match] ?? match);

/**
 * 判断一行是不是来源系统的 HTML 片段。
 * 只把有名字的标签当作 HTML（`<p>`、`<br/>`、`<span style=…>`），
 * 这样正文里随手写的 `<T>`、`a < b` 不会被误判。
 */
function looksLikeHtml(line: string): boolean {
  return /<\/?[a-z][a-z0-9]*(\s[^<>]*)?\/?>/i.test(line) || /&nbsp;|&amp;|&lt;|&gt;/i.test(line);
}

/**
 * 代码块里的来源系统 HTML → 纯文本段落（保留空行）。
 * 与 :func:`htmlBlock` 的区别：这里不生成 `<p>`，只做换行与去标签，
 * 因为外层已经是代码块，需要保留原始排版。
 */
function stripHtml(raw: string): string {
  const normalized = raw
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/t[dh]>/gi, ' ')
    .replace(/<li\b[^>]*>/gi, '• ')
    .replace(/<\/(p|div|li|tr|h[1-6]|blockquote)>/gi, '\n')
    .replace(/<(p|div|li|tr|h[1-6]|blockquote)\b[^>]*>/gi, '\n')
    .replace(/<[^>]*>/g, '');
  return decodeEntities(normalized)
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n');
}

/**
 * 把来源系统的一段 HTML 拆成若干「干净的纯文本段落」。
 *
 * `<br>` 保留为换行（由 `breaks` 决定是否转成 `<br>`），块级标签
 * （`<p>`、`<li>`…）落在段落边界上，其余标签（`<span style=…>` 等）直接去掉。
 * 返回的文本未转义，由调用方负责转义。
 */
function htmlBlock(
  raw: string,
  breaks: (html: string) => string,
  inline: (text: string) => string,
): string[] {
  const normalized = raw
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/t[dh]>/gi, ' ')
    .replace(/<li\b[^>]*>/gi, '\n• ')
    .replace(/<\/(p|div|li|tr|h[1-6]|blockquote)>/gi, '\n\n')
    .replace(/<(p|div|li|tr|h[1-6]|blockquote)\b[^>]*>/gi, '\n')
    .replace(/<[^>]*>/g, '');

  return decodeEntities(normalized)
    .split(/\n{2,}/)
    .map((chunk) =>
      chunk
        .split('\n')
        .map((line) => line.replace(/[ \t]+/g, ' ').trim())
        .filter(Boolean)
        .join('\n'),
    )
    .filter(Boolean)
    .map((chunk) => `<p>${breaks(inline(chunk))}</p>`);
}

export {esc};
