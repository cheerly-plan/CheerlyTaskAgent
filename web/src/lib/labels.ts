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
  failed: 'destructive-muted',
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

export function isArchived(item: {
  status: Status;
  exec_mode?: string;
  merge_state?: string;
}): boolean {
  // 自动合并冲突的任务虽然执行成功，但代码没进主仓——留在任务中心待处理，
  // 不能当历史归档掉。
  if (item.merge_state === 'blocked') return false;
  // 失败任务也留在任务中心，方便直接重试，不进历史。
  if (item.status === 'failed') return false;
  return item.exec_mode === 'apply' && TERMINAL_STATUSES.includes(item.status);
}

export function canEditConfig(item: {status: Status; is_container?: boolean}): boolean {
  if (item.is_container) return false;
  return item.status === 'discovered' || item.status === 'ready';
}

export const shortTime = (value?: string) => (value ?? '').slice(0, 16).replace('T', ' ');

export const sortFields = [
  {value: 'created', label: '创建时间'},
  {value: 'title', label: '标题'},
  {value: 'kind', label: '类型'},
  {value: 'source', label: '来源'},
  {value: 'repo', label: '项目'},
  {value: 'status', label: '状态'},
];