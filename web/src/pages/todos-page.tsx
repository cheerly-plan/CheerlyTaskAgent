import * as React from 'react';
import {
  AlertTriangle,
  CheckCheck,
  FileDiff,
  GitMerge,
  ListChecks,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-react';

import {useApp} from '@/app-context';
import {EmptyState} from '@/components/status-bits';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {endpoints, type TodoBatch, type TodoDetail, type TodoRow} from '@/lib/api';
import {cn} from '@/lib/utils';

type Filter = 'pending' | 'merged' | 'discarded' | 'all';

const FILTERS: {key: Filter; label: string}[] = [
  {key: 'pending', label: '待合并'},
  {key: 'merged', label: '已合并'},
  {key: 'discarded', label: '已放弃'},
  {key: 'all', label: '全部'},
];

const IMPACT: Record<string, {label: string; variant: 'outline' | 'success' | 'warning' | 'destructive' | 'muted'}> = {
  none: {label: '无影响', variant: 'outline'},
  low: {label: '低影响', variant: 'success'},
  medium: {label: '中影响', variant: 'warning'},
  high: {label: '高影响', variant: 'destructive'},
  unknown: {label: '未评估', variant: 'muted'},
};

const STATUS: Record<string, {label: string; variant: 'secondary' | 'success' | 'muted' | 'destructive'}> = {
  pending: {label: '待合并', variant: 'secondary'},
  merged: {label: '已合并', variant: 'success'},
  discarded: {label: '已放弃', variant: 'muted'},
  blocked: {label: '合并受阻', variant: 'destructive'},
};

function formatTime(value: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {hour12: false});
}

function TodoBadges({todo}: {todo: TodoRow}) {
  const impact = IMPACT[todo.impact_level] ?? IMPACT.unknown;
  const status = STATUS[todo.status] ?? STATUS.pending;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge variant={impact.variant}>{impact.label}</Badge>
      <Badge variant={status.variant}>{status.label}</Badge>
      {todo.review_source === 'rule' && <Badge variant="outline">规则评估</Badge>}
    </div>
  );
}

function TodoRowCard({
  todo,
  onOpen,
  onMerge,
  onDiscard,
  busy,
}: {
  todo: TodoRow;
  onOpen: () => void;
  onMerge: () => void;
  onDiscard: () => void;
  busy: boolean;
}) {
  const files = todo.diff_stat?.files ?? todo.changed_files.length;
  const added = todo.diff_stat?.added ?? 0;
  const removed = todo.diff_stat?.removed ?? 0;
  const pending = todo.status === 'pending' || todo.status === 'blocked';
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onOpen}
              className="truncate text-left text-[13.5px] font-semibold text-foreground hover:text-primary"
            >
              {todo.title}
            </button>
            {todo.repo && <Badge variant="outline">{todo.repo}</Badge>}
            <TodoBadges todo={todo} />
          </div>
          <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-foreground/70">
            {todo.summary || '（未生成汇总）'}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-[11.5px] text-faint">
            <span className="inline-flex items-center gap-1">
              <FileDiff className="size-3.5" />
              {files} 个文件
            </span>
            <span className="text-success-foreground">+{added}</span>
            <span className="text-destructive-foreground">-{removed}</span>
            <span>{formatTime(todo.created_at)}</span>
            {todo.batch_id && <span className="font-mono">批次 {todo.batch_id.replace('batch-', '')}</span>}
          </div>
          {todo.changed_files.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {todo.changed_files.slice(0, 5).map((file) => (
                <span key={file} className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                  {file}
                </span>
              ))}
              {todo.changed_files.length > 5 && (
                <span className="text-[11px] text-faint">+{todo.changed_files.length - 5}</span>
              )}
            </div>
          )}
          {todo.status !== 'merged' && todo.blockers.length > 0 && (
            <p className="mt-2 inline-flex items-start gap-1 text-[11.5px] text-warning-foreground">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              {todo.blockers.join('；')}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button size="sm" variant="outline" onClick={onOpen}>
            查看
          </Button>
          {pending && (
            <Button size="sm" onClick={onMerge} disabled={busy || !todo.can_merge}>
              <GitMerge />
              合并
            </Button>
          )}
          {pending && (
            <Button size="sm" variant="ghost" onClick={onDiscard} disabled={busy} aria-label="放弃">
              <Trash2 />
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

function BatchCard({batch}: {batch: TodoBatch}) {
  return (
    <Card className="border-primary/20 bg-primary-muted/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <ListChecks className="size-4 text-primary" />
          最近一批
          <span className="font-mono text-[11px] text-faint">{batch.id}</span>
        </h3>
        <span className="text-[11.5px] text-faint">
          {formatTime(batch.started_at)} → {batch.finished_at ? formatTime(batch.finished_at) : '进行中'}
        </span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-foreground/75">{batch.summary}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Badge variant="outline">任务 {batch.total}</Badge>
        <Badge variant="success">成功 {batch.succeeded}</Badge>
        {batch.failed > 0 && <Badge variant="destructive">失败 {batch.failed}</Badge>}
        <Badge variant="secondary">待办 {batch.todo_count}</Badge>
        <Badge variant="info">可合并 {batch.mergeable}</Badge>
        {batch.high_impact > 0 && <Badge variant="warning">高影响 {batch.high_impact}</Badge>}
        {batch.repos.length > 0 && (
          <span className="text-[11.5px] text-faint">仓库：{batch.repos.join('、')}</span>
        )}
      </div>
    </Card>
  );
}

export function TodosPage() {
  const {
    todos,
    todoCounts,
    todoBatches,
    todosLoading,
    mergeTodo,
    discardTodo,
    reloadTodos,
  } = useApp();

  const [filter, setFilter] = React.useState<Filter>('pending');
  const [detail, setDetail] = React.useState<TodoDetail | null>(null);
  const [diff, setDiff] = React.useState('');
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [refreshing, setRefreshing] = React.useState(false);

  const pendingCount = (todoCounts.pending ?? 0) + (todoCounts.blocked ?? 0);
  const visible = React.useMemo(() => {
    if (filter === 'all') return todos;
    if (filter === 'pending') return todos.filter((t) => t.status === 'pending' || t.status === 'blocked');
    return todos.filter((t) => t.status === filter);
  }, [filter, todos]);

  const openDetail = async (todo: TodoRow) => {
    setDetail({...todo, changes: [], review_raw: '', updated_at: todo.created_at});
    setDiff('');
    setDetailLoading(true);
    try {
      const [payload, diffPayload] = await Promise.all([
        endpoints.todoDetail(todo.id),
        endpoints.todoDiff(todo.id).catch(() => ({diff: '', stat: {}, worktree: ''})),
      ]);
      setDetail(payload.todo ?? null);
      setDiff(diffPayload.diff ?? '');
    } finally {
      setDetailLoading(false);
    }
  };

  const refresh = async () => {
    setRefreshing(true);
    try {
      await reloadTodos();
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-[15px] font-semibold tracking-[-0.01em] text-foreground">
            <CheckCheck className="size-4 text-primary" />
            我的待办
            {pendingCount > 0 && <Badge variant="secondary">{pendingCount}</Badge>}
          </h2>
          <p className="mt-1 text-xs text-faint">
            自动执行的结果先留在隔离 worktree，不写主仓库、不提交；在这里评审后点「合并」才会写回工作区。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => void refresh()} disabled={refreshing}>
            <RefreshCw className={cn(refreshing && 'animate-spin')} />
            刷新
          </Button>
        </div>
      </div>

      {todoBatches.length > 0 && <BatchCard batch={todoBatches[0]} />}

      <div className="flex flex-wrap items-center gap-1.5">
        {FILTERS.map((entry) => (
          <Button
            key={entry.key}
            size="sm"
            variant={filter === entry.key ? 'default' : 'outline'}
            onClick={() => setFilter(entry.key)}
          >
            {entry.label}
            {entry.key === 'pending' && pendingCount > 0 ? ` (${pendingCount})` : ''}
          </Button>
        ))}
      </div>

      {visible.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={GitMerge}
            title={filter === 'pending' ? '没有待合并的自动执行结果' : '这里还没有记录'}
            action={
              <span className="text-[11.5px] text-faint">
                TAPD 定时拉取的任务会自动执行；手动数据在任务中心勾选后点「自动执行」。
              </span>
            }
          />
        </Card>
      ) : (
        <div className="flex flex-col gap-2.5">
          {visible.map((todo) => (
            <TodoRowCard
              key={todo.id}
              todo={todo}
              busy={todosLoading}
              onOpen={() => void openDetail(todo)}
              onMerge={() => void mergeTodo(todo.id)}
              onDiscard={() => void discardTodo(todo.id)}
            />
          ))}
        </div>
      )}

      <Dialog open={detail !== null} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{detail?.title || '待办详情'}</DialogTitle>
            <DialogDescription>
              {detail?.repo ? `${detail.repo} · ` : ''}
              {detail ? formatTime(detail.created_at) : ''}
              {detail?.batch_id ? ` · 批次 ${detail.batch_id}` : ''}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-4">
            {detail && (
              <>
                <div className="flex flex-wrap items-center gap-1.5">
                  <TodoBadges todo={detail} />
                  <Badge variant={detail.can_merge ? 'success' : 'destructive'}>
                    {detail.can_merge ? '建议合并' : '不建议直接合并'}
                  </Badge>
                  {detail.confidence > 0 && (
                    <Badge variant="outline">置信度 {Math.round(detail.confidence * 100)}%</Badge>
                  )}
                </div>

                <section>
                  <h4 className="text-xs font-semibold text-foreground/80">汇总</h4>
                  <p className="mt-1 text-xs leading-relaxed text-foreground/75">
                    {detail.summary || '（未生成汇总）'}
                  </p>
                </section>

                {detail.changes.length > 0 && (
                  <section>
                    <h4 className="text-xs font-semibold text-foreground/80">改了哪些内容</h4>
                    <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-foreground/75">
                      {detail.changes.map((change, index) => (
                        <li key={`${change}-${index}`}>{change}</li>
                      ))}
                    </ul>
                  </section>
                )}

                <section>
                  <h4 className="text-xs font-semibold text-foreground/80">功能影响</h4>
                  <p className="mt-1 text-xs leading-relaxed text-foreground/75">
                    {detail.impact_notes || '未提供说明'}
                  </p>
                  {detail.impact_areas.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {detail.impact_areas.map((area) => (
                        <Badge key={area} variant="outline">{area}</Badge>
                      ))}
                    </div>
                  )}
                </section>

                {detail.blockers.length > 0 && (
                  <section className="rounded-lg border border-warning/40 bg-warning-muted/50 p-3">
                    <h4 className="flex items-center gap-1.5 text-xs font-semibold text-warning-foreground">
                      <AlertTriangle className="size-3.5" />
                      合并前需要处理
                    </h4>
                    <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-warning-foreground">
                      {detail.blockers.map((blocker, index) => (
                        <li key={`${blocker}-${index}`}>{blocker}</li>
                      ))}
                    </ul>
                  </section>
                )}

                <section>
                  <h4 className="text-xs font-semibold text-foreground/80">
                    改动文件（{detail.changed_files.length}）
                  </h4>
                  <div className="mt-1 max-h-40 overflow-y-auto rounded-md border border-border">
                    <table className="w-full text-left text-[11.5px]">
                      <tbody>
                        {detail.changed_files.map((file) => (
                          <tr key={file} className="border-b border-border/60 last:border-0">
                            <td className="px-2.5 py-1.5 font-mono text-foreground/75">{file}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>

                <section>
                  <h4 className="text-xs font-semibold text-foreground/80">Diff</h4>
                  {detailLoading ? (
                    <p className="mt-1 text-xs text-faint">正在读取 diff…</p>
                  ) : (
                    <pre className="scrollbar-thin mt-1 max-h-72 overflow-auto rounded-md border border-border bg-[#fbfbfd] p-3 text-[11px] leading-relaxed text-foreground/80">
                      {diff || '（没有可展示的 diff）'}
                    </pre>
                  )}
                </section>
              </>
            )}
          </DialogBody>
          <DialogFooter>
            {detail && (detail.status === 'pending' || detail.status === 'blocked') && (
              <>
                <Button
                  variant="outline"
                  onClick={() => {
                    void discardTodo(detail.id).then(() => setDetail(null));
                  }}
                >
                  <X />
                  放弃
                </Button>
                <Button
                  onClick={() => {
                    void mergeTodo(detail.id).then(() => setDetail(null));
                  }}
                  disabled={!detail.can_merge || todosLoading}
                >
                  <GitMerge />
                  合并到工作区
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}