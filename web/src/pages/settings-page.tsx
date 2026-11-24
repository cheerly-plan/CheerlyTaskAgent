import * as React from 'react';
import {GitPullRequest, Lock, Plus, RefreshCw, Save, Server, SlidersHorizontal, Sparkles, Trash2} from 'lucide-react';

import {useApp} from '@/app-context';
import {Field, SelectField} from '@/components/select-field';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table';
import {Switch} from '@/components/ui/switch';
import type {BackendConfig, DeliverySettings, ModelChoice, UISettings} from '@/lib/api';
import {
  canonicalModelId,
  cliDefaultLabel,
  cn,
  modelCategory,
  modelMatches,
  sortModelsByCategory,
} from '@/lib/utils';

const DEFAULT_DELIVERY: DeliverySettings = {
  mode: 'off',
  merge: 'manual',
  remote: 'origin',
  base: '',
  provider: '',
  api_base: '',
};

const DEFAULT_BACKEND: BackendConfig = {
  label: '',
  enabled: false,
  command: '',
  model: '',
  timeout_s: 600,
  auto_sync: true,
};

export function SettingsPage() {
  const {settings, saveSettings, reloadSettings, backends: backendInfos, ensureBackendProbe} = useApp();
  const [draft, setDraft] = React.useState<UISettings | null>(settings);

  React.useEffect(() => {
    setDraft(settings);
  }, [settings]);

  const modelsByBackend = React.useMemo(() => {
    const map: Record<string, ModelChoice[]> = {};
    for (const info of backendInfos) {
      map[info.id] = info.models ?? [];
    }
    return map;
  }, [backendInfos]);

  if (!draft) {
    return (
      <div className="flex items-center justify-center gap-2 py-20 text-sm text-faint">
        <span className="size-4 animate-spin rounded-full border-2 border-border border-t-primary" />
        正在载入设置…
      </div>
    );
  }

  const patch = <K extends keyof UISettings>(key: K, value: UISettings[K]) =>
    setDraft((prev) => (prev ? {...prev, [key]: value} : prev));
  const delivery = draft.delivery ?? DEFAULT_DELIVERY;
  const patchDelivery = <K extends keyof DeliverySettings>(key: K, value: DeliverySettings[K]) =>
    setDraft((prev) =>
      prev
        ? {...prev, delivery: {...DEFAULT_DELIVERY, ...(prev.delivery ?? {}), [key]: value}}
        : prev,
    );

  const backends = draft.backends ?? {};
  const backendEntries = Object.entries(backends);
  const getBackend = (id: string): BackendConfig => ({
    ...DEFAULT_BACKEND,
    ...(backends[id] ?? {}),
  });
  const patchBackend = (id: string, key: keyof BackendConfig, value: BackendConfig[keyof BackendConfig]) =>
    setDraft((prev) => {
      if (!prev) return prev;
      const current = getBackend(id);
      return {...prev, backends: {...(prev.backends ?? {}), [id]: {...current, [key]: value}}};
    });

  const addBackend = () =>
    setDraft((prev) => {
      if (!prev) return prev;
      const current = prev.backends ?? {};
      let seq = Object.keys(current).length + 1;
      let id = `backend-${seq}`;
      while (current[id]) {
        seq += 1;
        id = `backend-${seq}`;
      }
      return {
        ...prev,
        backends: {
          ...current,
          [id]: {...DEFAULT_BACKEND, label: `新后端 ${seq}`},
        },
      };
    });

  const removeBackend = (id: string) =>
    setDraft((prev) => {
      if (!prev) return prev;
      const next = {...(prev.backends ?? {})};
      delete next[id];
      const nextExecutor =
        prev.executor === id ? (Object.keys(next)[0] ?? '') : prev.executor;
      return {
        ...prev,
        backends: next,
        executor: nextExecutor,
      };
    });

  const backendChoices = backendEntries
    .filter(([, cfg]) => cfg.enabled)
    .map(([id, cfg]) => ({value: id, label: cfg.label || id}));

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <Card className="flex min-h-[200px] min-h-0 flex-col overflow-hidden">
        <div className="flex shrink-0 flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div>
            <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
              <Server className="size-4 text-primary" />
              执行后端
            </h3>
          </div>
          <Button variant="outline" size="sm" onClick={addBackend}>
            <Plus />
            添加后端
          </Button>
        </div>

        {backendEntries.length === 0 ? null : (
            <Table className="min-w-[960px] text-[12.5px]" containerClassName="min-h-0 flex-1">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-[220px]">名称</TableHead>
                  <TableHead>CLI 命令 / 路径</TableHead>
                  <TableHead className="w-[220px]">默认模型</TableHead>
                  <TableHead className="w-[120px]">超时（秒）</TableHead>
                  <TableHead className="w-[160px]">执行后同步</TableHead>
                  <TableHead className="w-[110px]">状态</TableHead>
                  <TableHead className="w-[72px] text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {backendEntries.map(([id]) => {
                  const current = getBackend(id);
                  const displayName = current.label || id;
                  const info = backendInfos.find((entry) => entry.id === id);
                  const canPickModel = info?.model_selection !== false;
                  const pinnedName = (info?.models ?? []).find((model) => model.current)?.id ?? '';
                  const cliDefault = cliDefaultLabel(modelsByBackend[id] ?? []);
                  const backendModels = modelsByBackend[id] ?? [];
                  const customModels = backendModels.filter((model) => model.custom);
                  return (
                    <TableRow key={id} className="[&>td]:align-top">
                      <TableCell>
                        <Input
                          className="h-8"
                          value={current.label}
                          onChange={(event) => patchBackend(id, 'label', event.target.value)}
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          className="h-8 font-mono"
                          value={current.command}
                          onChange={(event) => patchBackend(id, 'command', event.target.value)}
                        />
                      </TableCell>
                      <TableCell className="w-[220px] max-w-[220px]">
                        {canPickModel ? (
                          <SelectField
                            size="sm"
                            className="w-full"
                            value={canonicalModelId(backendModels, current.model)}
                            options={[
                              {value: '', label: cliDefault},
                              ...sortModelsByCategory(backendModels).map((model) => ({
                                value: model.id,
                                label: model.label ?? model.id,
                                group: modelCategory(model),
                              })),
                              ...(current.model && !backendModels.some((model) => modelMatches(model, current.model))
                                ? [{value: current.model, label: current.model}]
                                : []),
                            ]}
                            onOpenChange={(open) => {
                              if (open) ensureBackendProbe(id);
                            }}
                            onChange={(value) => patchBackend(id, 'model', value)}
                          />
                        ) : (
                          <div
                            className="flex h-8 w-full items-center gap-1.5 rounded-md border border-dashed border-border bg-[#fbfbfd] px-2 text-xs text-faint"
                          >
                            <Lock className="size-3 shrink-0" />
                            <span className="truncate">
                              profile 决定{pinnedName ? ` · ${pinnedName}` : ''}
                            </span>
                          </div>
                        )}
                        {canPickModel && backendModels.length > 0 ? (
                          <div className="mt-1 flex items-center gap-1 text-[11px] text-faint">
                            <Sparkles className={customModels.length ? 'size-3 text-[#6d3fc0]' : 'size-3'} />
                            <span>
                              内置 {backendModels.length - customModels.length} · 自定义{' '}
                              {customModels.length}
                            </span>
                          </div>
                        ) : null}
                      </TableCell>
                      <TableCell>
                        <Input
                          className="h-8 w-20 tabular-nums"
                          type="number"
                          min={1}
                          value={current.timeout_s}
                          onChange={(event) => patchBackend(id, 'timeout_s', Number(event.target.value))}
                        />
                      </TableCell>
                      <TableCell className="w-[160px]">
                        <div className="flex h-8 items-center gap-2">
                          <Switch
                            className="shrink-0"
                            checked={current.auto_sync}
                            onCheckedChange={(checked) => patchBackend(id, 'auto_sync', checked)}
                            aria-label={`${displayName} 执行后同步`}
                          />
                          <span className="w-16 shrink-0 whitespace-nowrap text-xs text-foreground/70">
                            {current.auto_sync ? '回写主仓库' : '仅 worktree'}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="flex h-8 items-center gap-2">
                          <Switch
                            checked={current.enabled}
                            onCheckedChange={(checked) => patchBackend(id, 'enabled', checked)}
                            aria-label={`${current.enabled ? '停用' : '启用'} ${displayName}`}
                          />
                          <span className={cn('whitespace-nowrap text-xs', current.enabled ? 'text-foreground/80' : 'text-faint')}>
                            {current.enabled ? '启用' : '停用'}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="danger"
                          size="icon-sm"
                          onClick={() => removeBackend(id)}
                          aria-label={`删除 ${displayName}`}
                        >
                          <Trash2 />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
        )}
      </Card>

      {backendEntries.length > 0 && (
        <Card className="shrink-0 p-5">
          <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground mb-5">
            <SlidersHorizontal className="size-4 text-primary" />
            调度策略
          </h3>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="默认后端" className="mb-0">
              <SelectField
                value={draft.executor || ''}
                options={backendChoices}
                onChange={(value) => patch('executor', value)}
              />
            </Field>
            <Field label="并发执行数" className="mb-0">
              <Input
                type="number"
                min={1}
                value={draft.max_concurrent}
                onChange={(event) => patch('max_concurrent', Number(event.target.value))}
              />
            </Field>
          </div>
        </Card>
      )}

      <Card className="shrink-0 p-5">
        <h3 className="mb-1 flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <GitPullRequest className="size-4 text-primary" />
          交付与 PR 合并
        </h3>
        <p className="mb-4 text-[11.5px] leading-relaxed text-foreground/55">
          这里是「全局默认」：项目映射里的交付 / 合并选择「跟随系统设置」时用这里的值。
          环境变量只作为首次启动的初始值，保存后以本页为准。
        </p>
        <div className="grid gap-4 md:grid-cols-3">
          <Field label="默认交付模式" className="mb-0">
            <SelectField
              value={delivery.mode}
              onChange={(value) => patchDelivery('mode', value as DeliverySettings['mode'])}
              options={[
                {value: 'off', label: '关闭（只写工作区）'},
                {value: 'commit', label: '提交任务分支'},
                {value: 'push', label: '提交并推送'},
                {value: 'pr', label: '推送并创建 PR/MR'},
              ]}
            />
          </Field>
          <Field label="PR 合并策略" className="mb-0">
            <SelectField
              value={delivery.merge}
              onChange={(value) => patchDelivery('merge', value as DeliverySettings['merge'])}
              options={[
                {value: 'manual', label: '人工合并'},
                {value: 'auto', label: 'CI 通过后自动合并'},
              ]}
            />
          </Field>
          <Field label="托管类型" className="mb-0">
            <SelectField
              value={delivery.provider}
              onChange={(value) => patchDelivery('provider', value as DeliverySettings['provider'])}
              options={[
                {value: '', label: '按远端域名自动识别'},
                {value: 'github', label: 'GitHub'},
                {value: 'gitlab', label: 'GitLab'},
              ]}
            />
          </Field>
          <Field label="远端名" className="mb-0">
            <Input
              value={delivery.remote}
              placeholder="origin"
              onChange={(event) => patchDelivery('remote', event.target.value)}
            />
          </Field>
          <Field label="PR 目标分支" className="mb-0">
            <Input
              value={delivery.base}
              placeholder="留空自动探测 main / master"
              onChange={(event) => patchDelivery('base', event.target.value)}
            />
          </Field>
          <Field label="企业版 API 地址" className="mb-0">
            <Input
              value={delivery.api_base}
              placeholder="留空使用官方 API"
              onChange={(event) => patchDelivery('api_base', event.target.value)}
            />
          </Field>
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-foreground/45">
          PR/MR 与 CI 需要 <code className="rounded bg-secondary px-1 py-0.5">GITHUB_TOKEN</code> 或{' '}
          <code className="rounded bg-secondary px-1 py-0.5">GITLAB_TOKEN</code>；
          自动合并只在「PR 模式 + CI 全绿」时生效，CI 失败不会合并。
        </p>
      </Card>

      <Card className="flex shrink-0 flex-wrap items-center gap-2 p-4">
        <Button size="sm" onClick={() => void saveSettings(draft)}>
          <Save />
          保存设置
        </Button>
        <Button size="sm" variant="outline" onClick={() => void reloadSettings()}>
          <RefreshCw />
          重新载入
        </Button>
      </Card>
    </div>
  );
}
