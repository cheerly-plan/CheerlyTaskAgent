import * as React from 'react';
import {Brain, GitPullRequest, Lock, Plus, RefreshCw, Save, Server, ShieldCheck, SlidersHorizontal, Sparkles, Trash2} from 'lucide-react';

import {useApp} from '@/app-context';
import {Field, SelectField} from '@/components/select-field';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table';
import {Tabs, TabsContent, TabsList, TabsTrigger} from '@/components/ui/tabs';
import {Switch} from '@/components/ui/switch';
import {endpoints} from '@/lib/api';
import type {
  AutomationSettings,
  BackendConfig,
  CircuitRepoState,
  DeliverySettings,
  ExecutorRoutingSettings,
  MemoryState,
  ModelChoice,
  UISettings,
} from '@/lib/api';
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

const DEFAULT_AUTOMATION: AutomationSettings = {
  allowed_kinds: [],
  repo_allowlist: [],
  failure_threshold: 3,
  cooldown_minutes: 30,
  max_recoveries: 1,
  decompose: false,
  max_subtasks: 4,
};

const DEFAULT_EXECUTOR_ROUTING: ExecutorRoutingSettings = {
  enabled: false,
  prompt: '',
  timeout_s: 60,
};

const AUTOMATION_KINDS = [
  {value: 'bug', label: '缺陷'},
  {value: 'feature', label: '需求'},
  {value: 'task', label: '任务'},
];

// 四个面板共用同一套容器样式，保证切换 tab 时布局不跳动：
// - pt-4 是统一约定的「卡片顶边 ↔ tab 栏底边」间距（16px），各 tab 完全一致；
// - flex-1 + min-h-0 + overflow-y-auto 让内容区独立滚动，保存栏得以固定在底部；
// - 只在选中时 flex，避免无条件 flex 覆盖 Radix 的 hidden 让空面板占位。
const TAB_PANEL_CLASS =
  'mt-0 min-h-0 flex-1 flex-col gap-4 overflow-y-auto pt-4 focus-visible:outline-none data-[state=active]:flex';

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
  const [circuit, setCircuit] = React.useState<Record<string, CircuitRepoState>>({});
  const [repoListText, setRepoListText] = React.useState('');
  const [memoryState, setMemoryState] = React.useState<MemoryState | null>(null);

  React.useEffect(() => {
    setDraft(settings);
  }, [settings]);

  React.useEffect(() => {
    let alive = true;
    endpoints
      .automationState()
      .then((state) => {
        if (alive) setCircuit(state.repos ?? {});
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [settings]);

  React.useEffect(() => {
    setRepoListText((settings?.automation?.repo_allowlist ?? []).join(', '));
  }, [settings?.automation?.repo_allowlist]);

  React.useEffect(() => {
    let alive = true;
    endpoints
      .memory()
      .then((state) => {
        if (alive) setMemoryState(state);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
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

  const automation = {...DEFAULT_AUTOMATION, ...(draft.automation ?? {})};
  const patchAutomation = <K extends keyof AutomationSettings>(
    key: K,
    value: AutomationSettings[K],
  ) =>
    setDraft((prev) =>
      prev
        ? {
            ...prev,
            automation: {...DEFAULT_AUTOMATION, ...(prev.automation ?? {}), [key]: value},
          }
        : prev,
    );
  const executorRouting = {...DEFAULT_EXECUTOR_ROUTING, ...(draft.executor_routing ?? {})};
  // 路由候选 = 已启用的后端；顺带展示各自的默认模型，规则里就能直接引用。
  const routingCandidates = Object.entries(draft.backends ?? {})
    .filter(([, cfg]) => cfg?.enabled)
    .map(([id, cfg]) => ({
      id,
      label: cfg.label || id,
      model: cfg.model || '后端默认',
    }));
  const routingDefault = routingCandidates.find((entry) => entry.id === draft.executor)
  const patchExecutorRouting = <K extends keyof ExecutorRoutingSettings>(
    key: K,
    value: ExecutorRoutingSettings[K],
  ) =>
    setDraft((prev) =>
      prev
        ? {
            ...prev,
            executor_routing: {
              ...DEFAULT_EXECUTOR_ROUTING,
              ...(prev.executor_routing ?? {}),
              [key]: value,
            },
          }
        : prev,
    );
  const toggleAutomationKind = (kind: string) => {
    const current = automation.allowed_kinds ?? [];
    patchAutomation(
      'allowed_kinds',
      current.includes(kind) ? current.filter((item) => item !== kind) : [...current, kind],
    );
  };
  const refreshMemory = async () => {
    try {
      setMemoryState(await endpoints.memory());
    } catch {
      // 读取失败保留旧视图，下次保存/刷新会重试。
    }
  };
  const clearMemory = async () => {
    if (!window.confirm('清空全部跨任务记忆？删除后不会影响已有任务，只是不再参考历史经验。')) return;
    try {
      await endpoints.clearMemory();
      setMemoryState(await endpoints.memory());
    } catch {
      // 失败不打断页面；下次刷新会重新读取。
    }
  };
  const resetCircuits = async () => {
    try {
      const result = await endpoints.resetCircuit();
      setCircuit(result.state.repos ?? {});
    } catch {
      // 重置失败不打断页面；下次保存/刷新会重新读取状态。
    }
  };
  const openCircuits = Object.entries(circuit).filter(
    ([, state]) => Boolean(state.open_until) && new Date(state.open_until as string) > new Date(),
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
      <Tabs defaultValue="backends" className="flex min-h-0 flex-1 flex-col">
        <div className="shrink-0">
          <TabsList className="w-full justify-start">
            <TabsTrigger
              value="backends"
              className="gap-1.5"
            >
              <Server className="size-3.5" />
              后端与调度
            </TabsTrigger>
            <TabsTrigger
              value="automation"
              className="gap-1.5"
            >
              <ShieldCheck className="size-3.5" />
              自动执行
            </TabsTrigger>
            <TabsTrigger
              value="memory"
              className="gap-1.5"
            >
              <Brain className="size-3.5" />
              任务记忆
            </TabsTrigger>
            <TabsTrigger
              value="delivery"
              className="gap-1.5"
            >
              <GitPullRequest className="size-3.5" />
              交付与合并
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="backends" className={TAB_PANEL_CLASS}>
      <Card className="flex min-h-[200px] flex-col overflow-hidden">
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
        </TabsContent>

        <TabsContent value="automation" className={TAB_PANEL_CLASS}>
      <Card className="p-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="任务类型" className="mb-0">
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant={automation.allowed_kinds.length === 0 ? 'subtle' : 'outline'}
                onClick={() => patchAutomation('allowed_kinds', [])}
              >
                全部
              </Button>
              {AUTOMATION_KINDS.map((kind) => (
                <Button
                  key={kind.value}
                  type="button"
                  size="sm"
                  variant={automation.allowed_kinds.includes(kind.value) ? 'subtle' : 'outline'}
                  onClick={() => toggleAutomationKind(kind.value)}
                >
                  {kind.label}
                </Button>
              ))}
            </div>
          </Field>
          <Field label="仓库白名单" className="mb-0">
            <Input
              value={repoListText}
              placeholder="留空 = 全部仓库"
              onChange={(event) => {
                const text = event.target.value;
                setRepoListText(text);
                patchAutomation(
                  'repo_allowlist',
                  text
                    .split(/[,，\n]/)
                    .map((item) => item.trim())
                    .filter(Boolean),
                );
              }}
            />
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-5 text-[12px] text-muted-foreground">
          <label className="flex items-center gap-2">
            <Switch
              checked={automation.decompose}
              onCheckedChange={(checked) => patchAutomation('decompose', checked)}
              aria-label="自动拆解多仓需求"
            />
            <span>自动拆解多仓需求</span>
          </label>
        </div>
        <div className="mt-4 grid gap-4 border-t border-border pt-4 md:grid-cols-4">
          <Field label="失败阈值" className="mb-0">
            <Input
              type="number"
              min={1}
              max={50}
              value={automation.failure_threshold}
              onChange={(event) =>
                patchAutomation('failure_threshold', Number(event.target.value))
              }
            />
          </Field>
          <Field label="冷却（分钟）" className="mb-0">
            <Input
              type="number"
              min={1}
              max={1440}
              value={automation.cooldown_minutes}
              onChange={(event) =>
                patchAutomation('cooldown_minutes', Number(event.target.value))
              }
            />
          </Field>
          <Field label="重排次数" className="mb-0">
            <Input
              type="number"
              min={0}
              max={10}
              value={automation.max_recoveries}
              onChange={(event) =>
                patchAutomation('max_recoveries', Number(event.target.value))
              }
            />
          </Field>
          <Field label="最多子任务" className="mb-0">
            <Input
              type="number"
              min={2}
              max={5}
              value={automation.max_subtasks}
              onChange={(event) =>
                patchAutomation('max_subtasks', Number(event.target.value))
              }
            />
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
          {openCircuits.length > 0 ? (
            <>
              <span>
                熔断中：
                {openCircuits
                  .map(([name, state]) => `${name}（${state.failures ?? 0} 次）`)
                  .join('、')}
              </span>
              <Button type="button" variant="ghost" size="sm" onClick={() => void resetCircuits()}>
                清除全部
              </Button>
            </>
          ) : null}
        </div>
      </Card>

      <Card className="p-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-1.5 text-sm font-medium">
              <Sparkles className="size-4" />
              LLM 路由执行后端
            </div>
            <p className="mt-1 text-[12px] text-muted-foreground">
              开启后，用「模型配置」里的后端 Agent 模型，按你写的规则为任务自动选后端与模型；
              显式指定的后端优先，调用失败自动降级到默认后端。模型不在该后端可用列表里、
              或该后端不支持覆盖模型时，会自动退回后端默认模型。
            </p>
          </div>
          <Switch
            checked={executorRouting.enabled}
            onCheckedChange={(value) => patchExecutorRouting('enabled', value)}
            aria-label="启用 LLM 路由"
          />
        </div>
        {routingCandidates.length > 0 ? (
          <div className="mt-3 rounded-md border border-border bg-card px-3 py-2 text-[12px] text-muted-foreground">
            <span className="text-faint">候选后端：</span>
            {routingCandidates.map((entry) => (
              <span key={entry.id} className="mr-3 inline-block">
                <span className="font-medium text-foreground">{entry.id}</span>
                <span className="text-faint"> / {entry.model}</span>
                {routingDefault?.id === entry.id ? (
                  <span className="ml-1 text-[11px] text-faint">（默认）</span>
                ) : null}
              </span>
            ))}
          </div>
        ) : null}
        <div className="mt-4 grid gap-4 md:grid-cols-[1fr_160px]">
          <Field label="路由规则（自然语言）" className="mb-0">
            <textarea
              className="min-h-[120px] w-full rounded-md border border-border bg-card px-3 py-2 text-[13px] text-foreground outline-none focus:ring-1 focus:ring-ring"
              placeholder={'例如：\n- bug 类且涉及接口/后端 → codebuddy，用强一点的模型\n- feature 页面/样式 → dsh 的 deepseek-v4.1-flash（快、便宜）\n- 简单文案修改 → dsh，model 留空用默认\n- 跨仓拆解的子任务 → codebuddy，保持上下文一致\n- 其他 → 默认后端'}
              value={executorRouting.prompt}
              onChange={(event) => patchExecutorRouting('prompt', event.target.value)}
            />
          </Field>
          <Field label="超时（秒）" className="mb-0">
            <Input
              type="number"
              min={5}
              max={600}
              value={executorRouting.timeout_s}
              onChange={(event) =>
                patchExecutorRouting('timeout_s', Number(event.target.value))
              }
            />
          </Field>
        </div>
      </Card>
        </TabsContent>

        <TabsContent value="memory" className={TAB_PANEL_CLASS}>
      <Card className="p-4">
        {memoryState ? (
          <>
            <div className="mb-4 grid gap-3 text-[12px] md:grid-cols-3">
              <div className="rounded-md border border-border bg-card px-3 py-2">
                <div className="text-faint">条目</div>
                <div className="mt-0.5 text-sm font-semibold text-foreground">
                  {memoryState.stats.total}
                </div>
              </div>
              <div className="rounded-md border border-border bg-card px-3 py-2">
                <div className="text-faint">成功率</div>
                <div className="mt-0.5 text-sm font-semibold text-foreground">
                  {Math.round(memoryState.stats.success_rate * 100)}%
                </div>
              </div>
              <div className="rounded-md border border-border bg-card px-3 py-2">
                <div className="text-faint">平均轮次</div>
                <div className="mt-0.5 text-sm font-semibold text-foreground">
                  {memoryState.stats.avg_rounds}
                </div>
              </div>
            </div>
            {memoryState.clusters.length > 0 ? (
              <ul className="mb-4 space-y-1.5 text-[12px] text-muted-foreground">
                {memoryState.clusters.slice(0, 5).map((cluster) => (
                  <li key={`${cluster.repo}-${cluster.signature}`} className="flex gap-2">
                    <span className="shrink-0 rounded bg-primary-muted px-1.5 py-0.5 text-[11px] text-accent-foreground">
                      {cluster.repo}
                    </span>
                    <span className="min-w-0 flex-1 truncate" title={cluster.summary}>
                      {cluster.summary} × {cluster.count}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="flex gap-2">
              <Button type="button" variant="ghost" size="sm" onClick={() => void refreshMemory()}>
                刷新
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void clearMemory()}
                disabled={memoryState.stats.total === 0}
              >
                清空记忆
              </Button>
            </div>
          </>
        ) : null}
      </Card>
        </TabsContent>

        <TabsContent value="delivery" className={TAB_PANEL_CLASS}>
      <Card className="p-4">
        <div className="grid gap-4 md:grid-cols-3">
          <Field label="交付模式" className="mb-0">
            <SelectField
              value={delivery.mode}
              onChange={(value) => patchDelivery('mode', value as DeliverySettings['mode'])}
              options={[
                {value: 'off', label: '关闭'},
                {value: 'commit', label: '仅提交'},
                {value: 'push', label: '提交并推送'},
                {value: 'pr', label: '创建 PR/MR'},
              ]}
            />
          </Field>
          <Field label="合并策略" className="mb-0">
            <SelectField
              value={delivery.merge}
              onChange={(value) => patchDelivery('merge', value as DeliverySettings['merge'])}
              options={[
                {value: 'manual', label: '人工'},
                {value: 'auto', label: 'CI 通过后自动'},
              ]}
            />
          </Field>
          <Field label="托管" className="mb-0">
            <SelectField
              value={delivery.provider}
              onChange={(value) => patchDelivery('provider', value as DeliverySettings['provider'])}
              options={[
                {value: '', label: '自动识别'},
                {value: 'github', label: 'GitHub'},
                {value: 'gitlab', label: 'GitLab'},
              ]}
            />
          </Field>
          <Field label="远端" className="mb-0">
            <Input
              value={delivery.remote}
              placeholder="origin"
              onChange={(event) => patchDelivery('remote', event.target.value)}
            />
          </Field>
          <Field label="目标分支" className="mb-0">
            <Input
              value={delivery.base}
              placeholder="自动"
              onChange={(event) => patchDelivery('base', event.target.value)}
            />
          </Field>
          <Field label="API 地址" className="mb-0">
            <Input
              value={delivery.api_base}
              placeholder="官方"
              onChange={(event) => patchDelivery('api_base', event.target.value)}
            />
          </Field>
        </div>
      </Card>
        </TabsContent>
      </Tabs>

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
