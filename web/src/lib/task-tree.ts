/**
 * 树形展示顺序：顶层项保持传入顺序，子项紧跟在其父后面。
 * 父不在当前集合里的「孤儿子需求」留在原位（不强行折叠），避免父子拆散。
 * 泛型，供 WorkItem 任务列表与 TapdPreviewItem 预览列表共用。
 *
 * 注意：父需求被删除后，子需求的 parent_ref 仍指向已不存在的父，
 * 这时它会被当作顶层项处理（不会丢失），但调用方应据此显示「父需求已删除」。
 */
export function orderForTreeView<T extends {source_ref?: string; parent_ref?: string}>(items: T[]): T[] {
  const refs = new Set(items.map((item) => item.source_ref).filter(Boolean));
  const byParent = new Map<string, T[]>();
  const roots: T[] = [];
  for (const item of items) {
    const pref = item.parent_ref;
    if (pref && pref !== item.source_ref && refs.has(pref)) {
      const bucket = byParent.get(pref) ?? [];
      bucket.push(item);
      byParent.set(pref, bucket);
    } else {
      roots.push(item);
    }
  }
  const ordered: T[] = [];
  for (const root of roots) {
    ordered.push(root);
    if (root.source_ref) ordered.push(...(byParent.get(root.source_ref) ?? []));
  }
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
