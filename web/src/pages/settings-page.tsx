import * as React from 'react';
import {Lock, Plus, RefreshCw, Save, Server, SlidersHorizontal, Sparkles, Trash2} from 'lucide-react';

import {useApp} from '@/app-context';
import {Field, SelectField} from '@/components/select-field';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {Switch} from '@/components/ui/switch';
import type {BackendConfig, ModelChoice, UISettings} from '@/lib/api';
import {
  canonicalModelId,
  cliDefaultLabel,
  modelCategory,
  modelHint,
  modelMatches,
  sortModelsByCategory,
} from '@/lib/utils';

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

  // 每个后端各自的模型列表（来自各自 CLI 探测），缺失时为空。
  // 必须在提前 return 之前声明，避免 Hooks 调用顺序不一致。
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

  // 新增后端：生成一个不与现有 id 冲突的标识，保存后由后端纳入注册表。
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

  // 删除后端：任何后端都可删除；若被删的正是默认后端，回退到第一个剩余后端（或空）。
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
    <div className="flex flex-col gap-4">
      {/* ── 执行后端：完全由配置驱动，可增删 ─────────────────────────── */}
      <div>
        <div className="mb-0.5 flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
              <Server className="size-4 text-primary" />
              执行后端
            </h3>
            <p className="mt-0.5 text-[11.5px] text-faint">
              后端由配置驱动，可按需添加或删除；只有启用的后端可被调用，没有后端时任务会因缺少执行器而失败。
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={addBackend}>
            <Plus />
            添加后端
          </Button>
        </div>

        {backendEntries.length === 0 ? (
          <Card className="mt-3 p-6 text-center text-[12px] text-faint">
            还没有配置执行后端，点击「添加后端」新建一个。
          </Card>
        ) : (
          <Card className="mt-3 overflow-hidden">
            <div className="max-h-[46vh] overflow-auto">
              <table className="w-full border-collapse text-[12.5px]">
                <thead className="sticky top-0 z-10 bg-card">
                  <tr className="border-b border-border text-left text-[11.5px] text-faint">
                    <th className="px-4 py-2.5 font-medium">名称</th>
                    <th className="px-4 py-2.5 font-medium">CLI 命令 / 路径</th>
                    <th className="w-[220px] px-4 py-2.5 font-medium">默认模型</th>
                    <th className="px-4 py-2.5 font-medium">超时（秒）</th>
                    <th className="w-[136px] px-4 py-2.5 font-medium">执行后同步</th>
                    <th className="px-4 py-2.5 font-medium">状态</th>
                    <th className="px-4 py-2.5 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {backendEntries.map(([id]) => {
                    const current = getBackend(id);
                    const displayName = current.label || id;
                    const info = backendInfos.find((entry) => entry.id === id);
                    // 有些 CLI（DSH）没有 --model：模型钉在它自己的 profile 里，只读展示。
                    const canPickModel = info?.model_selection !== false;
                    const pinnedName = (info?.models ?? []).find((model) => model.current)?.id ?? '';
                    const cliDefault = cliDefaultLabel(modelsByBackend[id] ?? []);
                    // 自定义模型 = 用户在 CLI 里自己配置的（codebuddy 的 models.json、
                    // DSH 手写网关路由），与 CLI 自带的内置模型分开统计与标注。
                    const backendModels = modelsByBackend[id] ?? [];
                    const customModels = backendModels.filter((model) => model.custom);
                    return (
                      <tr key={id} className="border-b border-border/70 align-top last:border-0">
                        <td className="px-4 py-3">
                          <Input
                            className="h-8"
                            value={current.label}
                            placeholder="给后端起个名字"
                            onChange={(event) => patchBackend(id, 'label', event.target.value)}
                          />
                        </td>
                        <td className="px-4 py-3">
                          <Input
                            className="h-8 font-mono"
                            value={current.command}
                            placeholder="例如 /usr/local/bin/my-agent"
                            onChange={(event) => patchBackend(id, 'command', event.target.value)}
                          />
                        </td>
                        <td className="w-[220px] max-w-[220px] px-4 py-3">
                          {canPickModel ? (
                            <SelectField
                              size="sm"
                              className="w-full"
                              value={canonicalModelId(backendModels, current.model)}
                              placeholder={cliDefault}
                              options={[
                                {value: '', label: cliDefault},
                                ...sortModelsByCategory(backendModels).map((model) => ({
                                  value: model.id,
                                  label: model.label ?? model.id,
                                  hint: modelHint(model),
                                  group: modelCategory(model),
                                })),
                                // 当前已保存的模型若不在动态列表里（含别名），仍保留以免丢失
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
                            // 该 CLI 没有 --model：模型钉在它自己的 profile 里，只读展示。
                            <div
                              className="flex h-8 w-full items-center gap-1.5 rounded-md border border-dashed border-border bg-[#fbfbfd] px-2 text-xs text-faint"
                              title={`${displayName} 不支持按任务指定模型，实际使用 profile 中钉住的模型`}
                            >
                              <Lock className="size-3 shrink-0" />
                              <span className="truncate">
                                profile 决定{pinnedName ? ` · ${pinnedName}` : ''}
                              </span>
                            </div>
                          )}
                          {canPickModel && backendModels.length > 0 ? (
                            <div
                              className="mt-1 flex items-center gap-1 text-[11px] text-faint"
                              title={customModels.length ? `自定义：${customModels.map((model) => model.label).join('、')}` : undefined}
                            >
                              <Sparkles className={customModels.length ? 'size-3 text-[#6d3fc0]' : 'size-3'} />
                              <span>
                                内置 {backendModels.length - customModels.length} · 自定义{' '}
                                {customModels.length}
                              </span>
                            </div>
                          ) : null}
                        </td>
                        <td className="px-4 py-3">
                          <Input
                            className="h-8 w-24"
                            type="number"
                            min={1}
                            value={current.timeout_s}
                            onChange={(event) => patchBackend(id, 'timeout_s', Number(event.target.value))}
                          />
                        </td>
                        <td className="w-[136px] px-4 py-3">
                          <div className="flex items-center gap-2">
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
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <Switch
                              checked={current.enabled}
                              onCheckedChange={(checked) => patchBackend(id, 'enabled', checked)}
                              aria-label={`启用 ${displayName}`}
                            />
                            <Badge variant={current.enabled ? 'success' : 'warning'}>
                              {current.enabled ? '已启用' : '未启用'}
                            </Badge>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Button
                            variant="danger"
                            size="icon-sm"
                            onClick={() => removeBackend(id)}
                            aria-label={`删除 ${displayName}`}
                            title="删除此后端"
                          >
                            <Trash2 />
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>

      {/* ── 调度策略：没有后端时无默认后端/并发可配置，隐藏 ─────────── */}
      {backendEntries.length > 0 && (
        <Card className="p-5">
          <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
            <SlidersHorizontal className="size-4 text-primary" />
            调度策略
          </h3>
          <p className="mb-4 mt-0.5 text-[11.5px] text-faint">控制默认后端、并发数量与执行后的全局同步行为。</p>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="新建任务默认后端" hint="手动建单预选的后端；未启用的后端不会出现" className="mb-0">
              <SelectField
                value={draft.executor || ''}
                options={backendChoices}
                onChange={(value) => patch('executor', value)}
              />
            </Field>
            <Field label="并发执行数" hint="同时运行的最大任务数" className="mb-0">
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

      {/* ── 操作栏 ───────────────────────────────────────────────── */}
      <Card className="flex flex-wrap items-center gap-2 p-4">
        <Button onClick={() => void saveSettings(draft)}>
          <Save />
          保存设置
        </Button>
        <Button variant="outline" onClick={() => void reloadSettings()}>
          <RefreshCw />
          重新载入
        </Button>
        <span className="ml-1 text-[11.5px] text-faint">
          后端的新增 / 删除、CLI 路径、默认模型与并发数在保存后立即生效；正在执行的任务不受影响。
        </span>
      </Card>
    </div>
  );
}
