import {clsx, type ClassValue} from 'clsx';
import {twMerge} from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

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

export function modelMatches(
  model: {id: string; aliases?: string[]},
  savedId: string | null | undefined,
): boolean {
  if (!savedId) return false;
  return model.id === savedId || (model.aliases?.includes(savedId) ?? false);
}

export function canonicalModelId(
  models: {id: string; aliases?: string[]}[],
  savedId: string | null | undefined,
): string {
  if (!savedId) return '';
  const hit = models.find((model) => modelMatches(model, savedId));
  return hit ? hit.id : savedId;
}

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

export function resolvedModelLabel(
  models: {id: string; label: string; aliases?: string[]}[],
  modelId: string | null | undefined,
): string {
  if (!modelId) return '—';
  const hit = models.find((model) => modelMatches(model, modelId));
  return hit?.label ?? modelId;
}

export function cliDefaultLabel(
  models: {id: string; label: string; current?: boolean}[],
): string {
  const current = models.find((model) => model.current);
  return current ? `跟随 CLI 当前（${current.label}）` : '由 CLI 决定';
}

export function modelCategory(model: {custom?: boolean}): string {
  return model.custom ? '自定义模型' : '内置模型';
}

export function sortModelsByCategory<T extends {custom?: boolean}>(models: T[]): T[] {
  return [...models].sort((a, b) => Number(Boolean(a.custom)) - Number(Boolean(b.custom)));
}
