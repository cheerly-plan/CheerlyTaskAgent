import type {WorkItem} from '@/lib/api';

/**
 * 任务列表的树形展示顺序：顶层项保持传入顺序，子项紧跟在其父后面。
 * 父不在当前集合里的「孤儿子需求」留在原位（不强行折叠），避免父子拆散。
 */
export function orderForTreeView(items: WorkItem[]): WorkItem[] {
  const refs = new Set(items.map((item) => item.source_ref).filter(Boolean));
  const byParent = new Map<string, WorkItem[]>();
  const roots: WorkItem[] = [];
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
  const ordered: WorkItem[] = [];
  for (const root of roots) {
    ordered.push(root);
    if (root.source_ref) ordered.push(...(byParent.get(root.source_ref) ?? []));
  }
  return ordered;
}
