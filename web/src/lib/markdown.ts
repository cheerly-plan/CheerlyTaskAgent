
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
      const innerLines = block.slice(1).filter(
        (current) =>
          !/^\s*<\/details>/i.test(current) &&
          !/<summary[^>]*>/i.test(current) &&
          !/^\s*<details\b/i.test(current),
      );
      const inner = renderMarkdown(innerLines.join('\n'));
      out.push(`<details><summary>${inline(summaryText)}</summary>${inner}</details>`);
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

    out.push(`<p>${inline(line)}</p>`);
    i++;
  }

  return out.join('\n');
}

export {esc};
