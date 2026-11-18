import {clsx, type ClassValue} from 'clsx';
import {twMerge} from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** 把模型的上下文窗口 token 数转成可读字符串，如 1M / 200K。 */
export function formatContext(tokens: number | undefined | null): string {
  if (tokens == null || !Number.isFinite(tokens) || tokens <= 0) return '';
  if (tokens >= 1_000_000) {
    const m = tokens / 1_000_000;
    return `${Number.isInteger(m) ? m : m.toFixed(1)}M`;
  }
  if (tokens >= 1_000) {
    const k = tokens / 1_000;
    return `${Number.isInteger(k) ? k : k.toFixed(0)}K`;
  }
  return `${tokens}`;
}

/** 模型下拉的 hint：当前使用 > 上下文窗口，可附加额外标记（如「全局默认」）。 */
export function modelHint(
  model: {context?: number; current?: boolean},
  extra?: string,
): string | undefined {
  const parts: string[] = [];
  if (model.current) parts.push('当前使用');
  if (extra) parts.push(extra);
  const ctx = formatContext(model.context);
  if (ctx) parts.push(`上下文 ${ctx}`);
  return parts.length ? parts.join(' · ') : undefined;
}

/** 已保存的模型 id 是否命中列表项（含去重折叠的别名）。 */
export function modelMatches(
  model: {id: string; aliases?: string[]},
  savedId: string | null | undefined,
): boolean {
  if (!savedId) return false;
  return model.id === savedId || (model.aliases?.includes(savedId) ?? false);
}

/** 把已保存的别名 id 归一到列表里的规范 id（去重后别名不再是独立选项）。 */
export function canonicalModelId(
  models: {id: string; aliases?: string[]}[],
  savedId: string | null | undefined,
): string {
  if (!savedId) return '';
  const hit = models.find((model) => modelMatches(model, savedId));
  return hit ? hit.id : savedId;
}

/**
 * 任务未显式指定模型时，该后端实际会落到哪个模型。
 * 优先级：后端 profile 的 default_model → CLI 当前使用的（current）→ 无。
 * 返回命中的模型 id（可能为空串）。
 */
export function effectiveDefaultModelId(
  models: {id: string; current?: boolean}[],
  backendDefault: string | null | undefined,
): string {
  if (backendDefault) {
    const hit = models.find((model) => modelMatches(model, backendDefault));
    if (hit) return hit.id;
    return backendDefault;
  }
  const current = models.find((model) => model.current);
  return current?.id ?? '';
}

/**
 * 空选项「默认」的展示文案：把模糊的「跟随全局」替换成实际会生效的模型名。
 * 后端 profile 有默认 → 显示其 label；否则 CLI 当前使用的模型；都没有 → 「CLI 决定」。
 */
export function defaultModelLabel(
  models: {id: string; label: string; current?: boolean; aliases?: string[]}[],
  backendDefault: string | null | undefined,
): string {
  if (backendDefault) {
    const hit = models.find((model) => modelMatches(model, backendDefault));
    if (hit) return `默认（${hit.label}）`;
    return `默认（${backendDefault}）`;
  }
  const current = models.find((model) => model.current);
  if (current) return `默认（${current.label}）`;
  return '默认（CLI 决定）';
}