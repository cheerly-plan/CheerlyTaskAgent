export function orderForTreeView<T extends {source_ref?: string; parent_ref?: string}>(items: T[]): T[] {
  const groups = groupByParent(items);
  const ordered: T[] = [];
  for (const group of groups) ordered.push(...group.items);
  return ordered;
}

export function orphanChildren<T extends {source_ref?: string; parent_ref?: string}>(
  items: T[],
): T[] {
  const refs = new Set(items.map((item) => item.source_ref).filter(Boolean));
  return items.filter(
    (item) => Boolean(item.parent_ref) && Boolean(item.source_ref) && !refs.has(item.parent_ref!),
  );
}

export interface TaskGroup<T> {
  key: string;
  parentTitle: string | null;
  items: T[];
}

export function groupByParent<
  T extends {source_ref?: string; parent_ref?: string; parent_title?: string; title?: string},
>(items: T[]): TaskGroup<T>[] {
  const groups: TaskGroup<T>[] = [];
  const byKey = new Map<string, TaskGroup<T>>();
  for (const item of items) {
    const key = item.parent_ref || '';
    if (!key) {
      groups.push({key: '', parentTitle: null, items: [item]});
      continue;
    }
    const existing = byKey.get(key);
    if (existing) {
      existing.items.push(item);
      if (!existing.parentTitle && item.parent_title) existing.parentTitle = item.parent_title;
      continue;
    }
    const group: TaskGroup<T> = {key, parentTitle: item.parent_title || null, items: [item]};
    byKey.set(key, group);
    groups.push(group);
  }
  return groups;
}
