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
      // 折叠块内部可能是最终结论的 Markdown（标题/列表/加粗），也可能是原始
      // 事件流的 ```text 代码块。交给同一个渲染器递归处理，避免把 Markdown
      // 标记当成纯文本显示。
      const innerLines = block.slice(1).filter(
        (current) => !/^\s*<\/details>/i.test(current) && !/<summary[^>]*>/i.test(current),
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

export interface ReportSection {
  label: string;
  html: string;
}

/**
 * 按顶层 `##` 标题把报告拆成多个板块，交给弹窗渲染成 tab。
 *
 * 代码块（```）与折叠块（<details>）内部的 `##` 只是报告正文，不能作为拆分点；
 * `#` 标题及其后的基本信息表归入第一个「基本信息」板块。
 */
export function renderReportSections(markdown: string): ReportSection[] {
  const lines = String(markdown || '').split('\n');
  const groups: {label: string; lines: string[]}[] = [];
  let current = {label: '基本信息', lines: [] as string[]};
  let inFence = false;
  let detailsDepth = 0;

  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      inFence = !inFence;
      current.lines.push(line);
      continue;
    }
    if (!inFence) {
      if (/^\s*<details\b/i.test(line)) {
        detailsDepth++;
      } else if (/^\s*<\/details>/i.test(line)) {
        detailsDepth = Math.max(0, detailsDepth - 1);
      }
      if (detailsDepth === 0) {
        const heading = line.match(/^##\s+(.*)$/);
        if (heading) {
          groups.push(current);
          current = {label: heading[1].trim(), lines: [line]};
          continue;
        }
      }
    }
    current.lines.push(line);
  }
  groups.push(current);

  return groups
    .filter((group) => group.lines.some((line) => line.trim()))
    .map((group) => ({label: group.label, html: renderMarkdown(group.lines.join('\n'))}));
}

export {esc};