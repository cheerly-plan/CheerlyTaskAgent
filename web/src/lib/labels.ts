import type {BadgeProps} from '@/components/ui/badge';
import type {Kind, Status} from '@/lib/api';

export const kindLabel: Record<string, string> = {bug: '缺陷', feature: '需求', task: '任务'};
export const statusLabel: Record<string, string> = {
  discovered: '待映射',
  ready: '就绪',
  queued: '排队',
  running: '执行中',
  succeeded: '成功',
  failed: '失败',
  skipped: '跳过',
  cancelled: '已取消',
};
export const sourceLabel: Record<string, string> = {manual: '手动', email: '邮件', tapd: 'TAPD'};

export const kindVariant: Record<Kind, BadgeProps['variant']> = {
  bug: 'destructive',
  feature: 'purple',
  task: 'info',
};

export const statusVariant: Record<Status, BadgeProps['variant']> = {
  discovered: 'outline',
  ready: 'warning',
  queued: 'warning',
  running: 'info',
  succeeded: 'success',
  failed: 'destructive',
  skipped: 'muted',
  cancelled: 'muted',
};

export const statusDot: Record<Status, string> = {
  discovered: '#b3b5c4',
  ready: '#d98a0b',
  queued: '#d98a0b',
  running: '#3d7fd1',
  succeeded: '#2e9e6b',
  failed: '#d64550',
  skipped: '#b3b5c4',
  cancelled: '#b3b5c4',
};

export const ACTIVE_STATUSES: Status[] = ['running', 'queued'];
export const TERMINAL_STATUSES: Status[] = ['succeeded', 'failed', 'skipped', 'cancelled'];

/** 归档到「执行历史」的记录：真实执行（apply）且已结束。dry-run 只是预演，留在任务列表。 */
export function isArchived(item: {status: Status; exec_mode?: string}): boolean {
  return item.exec_mode === 'apply' && TERMINAL_STATUSES.includes(item.status);
}

/** ISO 时间 → `YYYY-MM-DD HH:mm`（后端给的是 UTC ISO 串，沿用原有截断方式）。 */
export const shortTime = (value?: string) => (value ?? '').slice(0, 16).replace('T', ' ');

/** 卡片视图没有表头，用它列排序字段；升降序由旁边的箭头按钮切换。 */
export const sortFields = [
  {value: 'created', label: '创建时间'},
  {value: 'title', label: '标题'},
  {value: 'kind', label: '类型'},
  {value: 'source', label: '来源'},
  {value: 'repo', label: '项目'},
  {value: 'status', label: '状态'},
];