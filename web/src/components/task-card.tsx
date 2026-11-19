import {CornerDownRight, FolderGit2, GitBranch, Layers} from 'lucide-react';

import {Badge} from '@/components/ui/badge';
import {SelectField} from '@/components/select-field';
import {KindBadge, StatusBadge} from '@/components/status-bits';
import {TaskActions, type ActionSelection} from '@/components/task-actions';
import type {BackendInfo, ExecMode, ItemAction, ModelChoice, ProjectMapping, WorkItem} from '@/lib/api';
import {canEditConfig, sourceLabel} from '@/lib/labels';
import {cn} from '@/lib/utils';
import {effectiveDefaultModelId, modelMatches, resolvedModelLabel} from '@/lib/utils';

interface TaskCardProps {
  item: WorkItem;
  selection: ActionSelection;
  onSelect: (id: string, key: keyof ActionSelection, value: string) => void;
  onAction: (action: ItemAction, id: string) => void;
  onMap: (id: string, repo: string) => void;
  mappings: ProjectMapping[];
  models: ModelChoice[];
  backends: BackendInfo[];
  onProbe?: (executorId?: string) => void;
}

export function TaskCard({
  item,
  selection,
  onSelect,
  onAction,
  onMap,
  mappings,
  models,
  backends,
  onProbe,
}: TaskCardProps) {
  const isChild = Boolean(item.parent_ref);
  const isContainer = Boolean(item.is_container);
  const dryDone = item.status === 'succeeded' && item.exec_mode === 'dry-run';
  const backend = backends.find((entry) => entry.id === item.executor);
  const modelLocked = backend?.model_selection === false;
  const pinnedModel = modelLocked ? backend?.models?.find((model) => model.current)?.id : undefined;
  const repoOptions = mappings
    .filter((mapping) => mapping.enabled)
    .map((mapping) => ({value: mapping.name, label: mapping.name}));
  const mode = (selection.mode || item.exec_mode || 'dry-run') as ExecMode;
  const editable = canEditConfig(item);
  return (
    <div className={cn('group flex flex-col rounded-xl border bg-card p-4 shadow-sm transition-[border-color,box-shadow,transform] duration-150', isContainer ? 'border-[#e3d9f8] bg-[#faf9fd]' : 'border-border hover:-translate-y-px hover:border-primary/30 hover:shadow-md')}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-start gap-1.5">
            {isChild ? <CornerDownRight className="mt-0.5 size-3.5 shrink-0 text-[#a9a0c4]" aria-hidden /> : null}
            {isContainer ? <GitBranch className="mt-0.5 size-3.5 shrink-0 text-[#6d3fc0]" aria-hidden /> : null}
            <div className="line-clamp-2 text-[13.5px] font-semibold leading-snug text-foreground group-hover:text-primary">
              {item.title}
            </div>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px] text-faint">
            <span>{sourceLabel[item.source] ?? item.source}</span>
            {item.source_ref ? <span className="truncate">· {item.source_ref}</span> : null}
            {item.git_url ? <span className="truncate">· {item.git_url}</span> : null}
          </div>
        </div>
        <StatusBadge status={item.status} />
      </div>

      {isContainer ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          <KindBadge kind={item.kind} />
          <Badge variant="secondary">父需求</Badge>
        </div>
      ) : (
        <>
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          <KindBadge kind={item.kind} />
          {isContainer ? (
            <Badge variant="muted">—</Badge>
          ) : !editable ? (
            item.repo ? (
              <Badge variant="outline" className="gap-1">
                <FolderGit2 />
                {item.repo}
              </Badge>
            ) : (
              <Badge variant="muted">未映射</Badge>
            )
          ) : repoOptions.length ? (
            <SelectField
              size="sm"
              className="w-[120px]"
              value={item.repo ?? ''}
              placeholder="选择仓库"
              options={repoOptions}
              onChange={(value) => onMap(item.id, value)}
              title="映射仓库"
            />
          ) : item.repo ? (
            <Badge variant="outline" className="gap-1">
              <FolderGit2 />
              {item.repo}
            </Badge>
          ) : (
            <Badge variant="muted">未映射</Badge>
          )}
          {isContainer ? null : !editable ? (
            <Badge variant="secondary" className="font-mono">
              {mode}
            </Badge>
          ) : (
            <SelectField
              size="sm"
              className="w-[92px]"
              value={mode}
              options={[
                {value: 'dry-run', label: 'dry-run'},
                {value: 'apply', label: 'apply'},
              ]}
              onChange={(value) => onSelect(item.id, 'mode', value)}
              title="执行方式"
            />
          )}
          {isContainer ? null : modelLocked ? (
            <Badge variant="muted" title={`由 ${backend?.label ?? ''} profile 决定`}>
              profile 决定{pinnedModel ? ` · ${pinnedModel}` : ''}
            </Badge>
          ) : (
            (() => {
              const list = backend?.models?.length ? backend.models : models;
              const modelId = item.model || effectiveDefaultModelId(list, backend?.default_model);
              const custom = list.some((model) => modelMatches(model, modelId) && model.custom);
              return (
                <Badge variant="secondary" className="font-mono">
                  {resolvedModelLabel(list, modelId)}
                  {custom ? ' · 自定义' : ''}
                </Badge>
              );
            })()
          )}
          {dryDone ? <Badge variant="warning">待 apply</Badge> : null}
        </div>

        {item.stage ? (
          <div className="mt-2.5 flex items-start gap-1.5 text-[12px] leading-relaxed text-foreground/70">
            <Layers className="mt-0.5 size-3.5 shrink-0 text-faint" />
            <span className="line-clamp-2">{item.stage}</span>
          </div>
        ) : null}

        {item.error ? (
          <div className="mt-2 line-clamp-2 rounded-md bg-destructive-muted px-2 py-1.5 text-[12px] leading-relaxed text-destructive">
            {item.error}
          </div>
        ) : null}

        <div className="mt-3 border-t border-border/70 pt-3">
          <TaskActions
            compact
            item={item}
            selection={selection}
            onSelect={(key, value) => onSelect(item.id, key, value)}
            onAction={onAction}
            models={models}
            backends={backends}
            onProbe={onProbe}
            container={isContainer}
          />
        </div>
        </>
      )}
    </div>
  );
}