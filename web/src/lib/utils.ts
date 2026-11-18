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

/** 模型下拉的 hint：当前使用 > 全局默认 > 自定义 > 上下文窗口。 */
export function modelHint(
  model: {context?: number; current?: boolean; custom?: boolean},
  extra?: string,
): string | undefined {
  const parts: string[] = [];
  if (model.current) parts.push('当前使用');
  if (extra) parts.push(extra);
  if (model.custom) parts.push('自定义');
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
 * 优先级：后端 profile 的 default_model → CLI 当前使用的（current）→ 列表第一个。
 * 只要模型列表非空就返回具体 id，保证下拉/回填不会出现空值；
 * 列表为空时返回空串（此时调用方应禁用模型选择）。
 */
export function effectiveDefaultModelId(
  models: {id: string; current?: boolean; aliases?: string[]}[],
  backendDefault: string | null | undefined,
): string {
  if (backendDefault) {
    const hit = models.find((model) => modelMatches(model, backendDefault));
    if (hit) return hit.id;
    return backendDefault;
  }
  const current = models.find((model) => model.current);
  if (current) return current.id;
  return models[0]?.id ?? '';
}

/**
 * 已落地模型的纯展示名：拿到具体模型 id 后取其 label（找不到则回显 id）。
 * 任务一旦创建就带具体模型，展示位直接显示模型名，不再有「默认（X）」这类模糊包装。
 */
export function resolvedModelLabel(
  models: {id: string; label: string; aliases?: string[]}[],
  modelId: string | null | undefined,
): string {
  if (!modelId) return '—';
  const hit = models.find((model) => modelMatches(model, modelId));
  return hit?.label ?? modelId;
}

/**
 * 系统设置页「默认模型」空选项的文案。空值表示不给后端传 --model，
 * 由 CLI 自己决定 —— 而 CLI 当前实际用的就是被标记 current 的模型，
 * 所以直接把它显示出来，避免「由 CLI 决定」这种含糊说法。
 */
export function cliDefaultLabel(
  models: {id: string; label: string; current?: boolean}[],
): string {
  const current = models.find((model) => model.current);
  return current ? `跟随 CLI 当前（${current.label}）` : '由 CLI 决定';
}