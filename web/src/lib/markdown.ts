
const esc = (value: unknown) =>
  String(value ?? '').replace(/[&<>"']/g, (c) => {
    const map: Record<string, string> = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
    return map[c];
  });

export function renderMarkdown(markdown: string): string {
  const lines = String(markdown || '').split('\n');
  const out: string[] = [];
  let i = 0;

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
      const inner = renderMarkdown(innerLines.join('\n'));
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
      out.push(`<p>${inlineTagged(sanitizeInline(line))}</p>`);
      i++;
      continue;
    }

    out.push(`<p>${inline(line)}</p>`);
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
 * 把来源系统的一行 HTML 收敛成安全的行内内容：
 * 块级标签变成空格，`<br>` 变成换行，其余不认识的标签直接丢掉。
 * 输出未转义的纯文本（由调用方负责转义）。
 */
function sanitizeInline(raw: string): string {
  let text = raw;
  text = text.replace(/<br\s*\/?>/gi, '\n');
  text = text.replace(/<\/(p|div|li|tr|h[1-6])>/gi, '\n');
  text = text.replace(/<\/t[dh]>/gi, ' ');
  text = text.replace(/<li\b[^>]*>/gi, '• ');
  text = text.replace(/<[^>]*>/g, '');
  return decodeEntities(text)
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export {esc};
