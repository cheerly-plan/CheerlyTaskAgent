import * as React from 'react';
import {RefreshCw, Save, Server, SlidersHorizontal} from 'lucide-react';

import {useApp} from '@/app-context';
import {Field, SelectField} from '@/components/select-field';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {Switch} from '@/components/ui/switch';
import type {BackendConfig, UISettings} from '@/lib/api';

const BACKEND_META: Record<string, {label: string; desc: string; defaultCommand: string}> = {
  codebuddy: {
    label: 'CodeBuddy CLI',
    desc: '当前默认执行器，始终可用作兜底。',
    defaultCommand: 'codebuddy',
  },
  workbuddy: {
    label: 'WorkBuddy CLI',
    desc: '与 CodeBuddy 使用相同 CLI 协议，配置二进制路径后启用。',
    defaultCommand: 'workbuddy',
  },
  dsh: {
    label: 'DSH',
    desc: '内部 DSH 执行器，配置二进制路径后启用。',
    defaultCommand: 'dsh',
  },
};
const BACKEND_IDS = Object.keys(BACKEND_META);

const DEFAULT_BACKEND: BackendConfig = {
  enabled: false,
  command: '',
  model: '',
  timeout_s: 600,
  auto_sync: true,
};

export function SettingsPage() {
  const {settings, saveSettings, reloadSettings} = useApp();
  const [draft, setDraft] = React.useState<UISettings | null>(settings);

  React.useEffect(() => {
    setDraft(settings);
  }, [settings]);

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
  const getBackend = (id: string): BackendConfig => ({
    ...DEFAULT_BACKEND,
    ...(backends[id] ?? {}),
    command: backends[id]?.command || BACKEND_META[id].defaultCommand,
  });
  const patchBackend = (id: string, key: keyof BackendConfig, value: BackendConfig[keyof BackendConfig]) =>
    setDraft((prev) => {
      if (!prev) return prev;
      const current = getBackend(id);
      return {...prev, backends: {...(prev.backends ?? {}), [id]: {...current, [key]: value}}};
    });

  const backendChoices = BACKEND_IDS.filter((id) => id === 'codebuddy' || getBackend(id).enabled).map((id) => ({
    value: id,
    label: BACKEND_META[id].label,
  }));

  return (
    <div className="flex flex-col gap-4">
      {/* ── 执行后端：每类一套独立配置 ───────────────────────────── */}
      <div>
        <h3 className="mb-0.5 flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <Server className="size-4 text-primary" />
          执行后端
        </h3>
        <p className="mb-3 text-[11.5px] text-faint">
          三类后端各自独立配置。任务创建时选择用哪一类；只有启用的后端可被调用，CodeBuddy 始终作为兜底。
        </p>
        <div className="grid gap-4 lg:grid-cols-3">
          {BACKEND_IDS.map((id) => {
            const meta = BACKEND_META[id];
            const cfg = getBackend(id);
            const alwaysOn = id === 'codebuddy';
            return (
              <Card key={id} className="p-5">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <h4 className="text-[13px] font-semibold text-foreground">{meta.label}</h4>
                  <div className="flex items-center gap-2">
                    <Badge variant={alwaysOn || cfg.enabled ? 'success' : 'warning'}>
                      {alwaysOn ? '始终启用' : cfg.enabled ? '已启用' : '未启用'}
                    </Badge>
                    <Switch
                      checked={alwaysOn ? true : cfg.enabled}
                      disabled={alwaysOn}
                      onCheckedChange={(checked) => patchBackend(id, 'enabled', checked)}
                      aria-label={`启用 ${meta.label}`}
                    />
                  </div>
                </div>
                <p className="mb-4 min-h-[30px] text-[11px] leading-[1.4] text-faint">{meta.desc}</p>

                <Field label="CLI 命令 / 路径">
                  <Input
                    className="font-mono"
                    value={cfg.command}
                    placeholder={meta.defaultCommand}
                    onChange={(event) => patchBackend(id, 'command', event.target.value)}
                  />
                </Field>
                <Field label="默认模型" hint="留空跟随全局；任务可单独覆盖">
                  <Input
                    className="font-mono"
                    value={cfg.model}
                    placeholder="留空使用全局默认"
                    onChange={(event) => patchBackend(id, 'model', event.target.value)}
                  />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="超时（秒）" className="mb-0">
                    <Input
                      type="number"
                      min={1}
                      value={cfg.timeout_s}
                      onChange={(event) => patchBackend(id, 'timeout_s', Number(event.target.value))}
                    />
                  </Field>
                  <div>
                    <span className="mb-1.5 block text-xs font-semibold tracking-wide text-foreground/75">
                      执行后同步
                    </span>
                    <div className="flex h-9 items-center gap-2.5 rounded-lg border border-border bg-[#fbfbfd] px-3">
                      <Switch
                        checked={cfg.auto_sync}
                        onCheckedChange={(checked) => patchBackend(id, 'auto_sync', checked)}
                        aria-label={`${meta.label} 执行后同步`}
                      />
                      <span className="text-xs text-foreground/70">{cfg.auto_sync ? '回写主仓库' : '仅 worktree'}</span>
                    </div>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      </div>

      {/* ── 调度策略 ─────────────────────────────────────────────── */}
      <Card className="p-5">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <SlidersHorizontal className="size-4 text-primary" />
          调度策略
        </h3>
        <p className="mb-4 text-[11.5px] text-faint">控制默认后端、并发数量与执行后的全局同步行为。</p>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="新建任务默认后端" hint="手动建单预选的后端；未启用的后端不会出现">
            <SelectField
              value={draft.executor || 'codebuddy'}
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
          CLI 路径、默认模型等运行时参数在保存后需重启服务才会对新任务生效。
        </span>
      </Card>
    </div>
  );
}
