import * as React from 'react';
import {Inbox, RefreshCw, Save, Settings2, Terminal} from 'lucide-react';

import {useApp} from '@/app-context';
import {Field, SelectField} from '@/components/select-field';
import {Alert, AlertDescription} from '@/components/ui/alert';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {Switch} from '@/components/ui/switch';
import type {UISettings} from '@/lib/api';

export function SettingsPage() {
  const {settings, models, defaultModel, saveSettings, reloadSettings} = useApp();
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
  const patchImap = <K extends keyof UISettings['imap']>(key: K, value: UISettings['imap'][K]) =>
    setDraft((prev) => (prev ? {...prev, imap: {...prev.imap, [key]: value}} : prev));

  const modelOptions = [
    {value: '', label: '默认（跟随全局）'},
    ...models.map((model) => ({
      value: model.id,
      label: model.label,
      hint: model.id === defaultModel ? '全局默认模型' : undefined,
    })),
  ];

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card className="p-5">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <Terminal className="size-4 text-primary" />
          执行后端
        </h3>
        <p className="mb-4 text-[11.5px] text-faint">决定调度时调用的 CLI 与默认模型。</p>

        <Field label="当前执行器">
          <SelectField
            value={draft.executor || 'codebuddy'}
            onChange={(value) => patch('executor', value)}
            options={[
              {value: 'codebuddy', label: 'CodeBuddy CLI（当前启用）'},
              {value: 'workbuddy', label: 'WorkBuddy CLI（预留）'},
              {value: 'dsh', label: 'DSH（预留）'},
            ]}
          />
        </Field>

        <Field label="CodeBuddy CLI 命令 / 路径">
          <Input
            className="font-mono"
            value={draft.codebuddy_command}
            placeholder="codebuddy"
            onChange={(event) => patch('codebuddy_command', event.target.value)}
          />
        </Field>

        <Field label="WorkBuddy CLI 路径（预留）">
          <Input
            className="font-mono"
            value={draft.workbuddy_cli_path}
            placeholder="/path/to/workbuddy"
            onChange={(event) => patch('workbuddy_cli_path', event.target.value)}
          />
        </Field>

        <Field label="DSH 路径（预留）">
          <Input
            className="font-mono"
            value={draft.dsh_path}
            placeholder="/path/to/dsh"
            onChange={(event) => patch('dsh_path', event.target.value)}
          />
        </Field>

        <Field label="默认模型" hint="新建任务或工单未指定模型时使用。">
          <SelectField
            value={draft.default_model}
            options={modelOptions}
            onChange={(value) => patch('default_model', value)}
          />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="并发执行数">
            <Input
              type="number"
              min={1}
              value={draft.max_concurrent}
              onChange={(event) => patch('max_concurrent', Number(event.target.value))}
            />
          </Field>
          <div>
            <span className="mb-1.5 block text-xs font-semibold tracking-wide text-foreground/75">
              执行后自动同步
            </span>
            <div className="flex h-9 items-center gap-2.5 rounded-lg border border-border bg-[#fbfbfd] px-3">
              <Switch
                checked={draft.auto_sync}
                onCheckedChange={(checked) => patch('auto_sync', checked)}
                aria-label="执行后自动同步"
              />
              <span className="truncate text-xs text-foreground/70">
                {draft.auto_sync ? '开启：改动回写到主仓库' : '关闭：仅保留在 worktree'}
              </span>
            </div>
          </div>
        </div>
      </Card>

      <Card className="p-5">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <Inbox className="size-4 text-primary" />
          IMAP 邮箱凭证
        </h3>
        <p className="mb-4 text-[11.5px] text-faint">用于从收件箱拉取工单（凭证仅保存在本机）。</p>

        <Field label="IMAP 主机">
          <Input
            className="font-mono"
            value={draft.imap.host}
            placeholder="imap.example.com"
            onChange={(event) => patchImap('host', event.target.value)}
          />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="端口">
            <Input
              type="number"
              min={1}
              value={draft.imap.port}
              onChange={(event) => patchImap('port', Number(event.target.value))}
            />
          </Field>
          <Field label="安全连接">
            <SelectField
              value={draft.imap.use_ssl === false ? '0' : '1'}
              options={[
                {value: '1', label: 'SSL / TLS'},
                {value: '0', label: '明文 / STARTTLS'},
              ]}
              onChange={(value) => patchImap('use_ssl', value === '1')}
            />
          </Field>
        </div>

        <Field label="邮箱账号">
          <Input
            className="font-mono"
            value={draft.imap.user}
            placeholder="you@example.com"
            onChange={(event) => patchImap('user', event.target.value)}
          />
        </Field>

        <Field label="密码 / 授权码">
          <Input
            type="password"
            value={draft.imap.password}
            placeholder="留空表示保持已保存值不变"
            onChange={(event) => patchImap('password', event.target.value)}
          />
        </Field>

        <Alert variant="warning">
          <AlertDescription>
            凭证以明文保存在本机 data/settings.json，请注意文件权限；密码留空即可保留原值。
          </AlertDescription>
        </Alert>

        <div className="mt-4 rounded-lg border border-dashed border-border bg-[#fbfbfd] px-3.5 py-3">
          <h4 className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-foreground/80">
            <Settings2 className="size-3.5 text-faint" />
            扩展能力
          </h4>
          <p className="text-[11.5px] leading-relaxed text-foreground/70">
            当前支持邮件、手动录入两类工单，调度本地 CLI 自动处理 Bug 与需求；TAPD 连接器为预留扩展点，暂未启用。
          </p>
        </div>
      </Card>

      <div className="flex flex-wrap items-center gap-2 xl:col-span-2">
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
      </div>
    </div>
  );
}