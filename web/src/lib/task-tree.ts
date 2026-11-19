/**
 * 树形展示顺序：顶层项保持传入顺序，子项紧跟在其父后面。
 * 父不在当前集合里的「孤儿子需求」留在原位（不强行折叠），避免父子拆散。
 * 泛型，供 WorkItem 任务列表与 TapdPreviewItem 预览列表共用。
 *
 * 注意：父需求被删除后，子需求的 parent_ref 仍指向已不存在的父，
 * 这时它会被当作顶层项处理（不会丢失），但调用方应据此显示「父需求已删除」。
 */
export function orderForTreeView<T extends {source_ref?: string; parent_ref?: string}>(items: T[]): T[] {
  // 按父需求聚合：同一父的子需求连在一起，父不在本地时也能聚合。
  const groups = groupByParent(items);
  const ordered: T[] = [];
  for (const group of groups) ordered.push(...group.items);
  return ordered;
}

/**
 * 找出「父不在当前集合里」的子需求。
 * 这类工单要么父还没导入，要么父已被删除，UI 需要区分提示。
 */
export function orphanChildren<T extends {source_ref?: string; parent_ref?: string}>(
  items: T[],
): T[] {
  const refs = new Set(items.map((item) => item.source_ref).filter(Boolean));
  return items.filter(
    (item) => Boolean(item.parent_ref) && Boolean(item.source_ref) && !refs.has(item.parent_ref!),
  );
}

/** 按父需求分组的展示块：父标题 + 该父下的子需求（或独立的单条工单）。 */
export interface TaskGroup<T> {
  /** 父需求 source_ref；顶层/孤儿为空。 */
  key: string;
  /** 组标题（父需求标题）；无父时为 null。 */
  parentTitle: string | null;
  items: T[];
}

/**
 * 把工单列表按父需求合并成组。
 *
 * 父需求可能根本不在本地（同步时只导入了子需求），所以分组依据是
 * 子需求自己存的 ``parent_ref`` + ``parent_title``，而不是「本地是否存在父行」。
 * 同父的子需求合到一起，按父分组；没有父需求的工单各自成组。
 * 组顺序按组内最早出现的工单保持原排序。
 */
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
