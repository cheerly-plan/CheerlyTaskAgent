/**
 * 报告 Markdown 渲染器（轻量、无第三方依赖）。
 *
 * 只覆盖后端 `report.py` 生成的结构：标题、表格、围栏代码、引用、列表，
 * 以及报告里自定义的 <details><summary> 折叠块。输出为 HTML 字符串，
 * 由 ReportDialog 通过 dangerouslySetInnerHTML 注入 `.report-body`。
 */

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

    // 后端报告里可能带 <details> 折叠块：原样解析，避免被 inline() 二次转义
    if (/^\s*<details\b/i.test(line)) {
      const block: string[] = [];
      while (i < lines.length) {
        block.push(lines[i]);
        if (/^\s*<\/details>/i.test(lines[i])) {
          i++;
          break;
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
      let inner = '';
      let k = 1;
      while (k < block.length) {
        const current = block[k];
        if (/^\s*<\/details>/i.test(current) || /<summary[^>]*>/i.test(current)) {
          k++;
          continue;
        }
        if (/^\s*```/.test(current)) {
          k++;
          const code: string[] = [];
          while (k < block.length && !/^\s*```/.test(block[k])) {
            code.push(block[k]);
            k++;
          }
          k++;
          inner += `<pre class="md-code">${esc(code.join('\n'))}</pre>`;
        } else if (current.trim()) {
          inner += `<p>${inline(current)}</p>`;
          k++;
        } else {
          k++;
        }
      }
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