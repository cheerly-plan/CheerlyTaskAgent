import * as React from 'react';
import {Inbox, RefreshCw, Save, Webhook} from 'lucide-react';

import {useApp} from '@/app-context';
import {Field, SelectField} from '@/components/select-field';
import {Alert, AlertDescription} from '@/components/ui/alert';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {Switch} from '@/components/ui/switch';
import {endpoints, type UISettings} from '@/lib/api';
import {toast} from 'sonner';

const TAPD_DEFAULT: UISettings['tapd'] = {
  enabled: false,
  base_url: 'https://api.tapd.cn',
  workspace_id: '',
  api_user: '',
  api_password: '',
  webhook_secret: '',
  entity_types: ['story', 'bug'],
  pull_statuses: [],
  status_on_success: 'resolved',
  status_on_failure: '',
  current_user: 'taskagent',
  interval_minutes: 5,
  exec_mode: 'dry-run',
};

export function DataSourcesPage() {
  const {settings, saveSettings, reloadSettings, pullMail, syncTapd} = useApp();
  const [draft, setDraft] = React.useState<UISettings | null>(settings);
  const [pulling, setPulling] = React.useState(false);
  const [syncing, setSyncing] = React.useState(false);

  React.useEffect(() => {
    setDraft(settings);
  }, [settings]);

  if (!draft) {
    return (
      <div className="flex items-center justify-center gap-2 py-20 text-sm text-faint">
        <span className="size-4 animate-spin rounded-full border-2 border-border border-t-primary" />
        正在载入数据源…
      </div>
    );
  }

  const patchImap = <K extends keyof UISettings['imap']>(key: K, value: UISettings['imap'][K]) =>
    setDraft((prev) => (prev ? {...prev, imap: {...prev.imap, [key]: value}} : prev));
  const mailPoll: UISettings['mail_poll'] = draft.mail_poll ?? {
    enabled: false,
    interval_minutes: 5,
    exec_mode: 'dry-run',
  };
  const patchPoll = <K extends keyof UISettings['mail_poll']>(key: K, value: UISettings['mail_poll'][K]) =>
    setDraft((prev) => (prev ? {...prev, mail_poll: {...(prev.mail_poll ?? mailPoll), [key]: value}} : prev));
  const tapd: UISettings['tapd'] = draft.tapd ?? TAPD_DEFAULT;
  const patchTapd = <K extends keyof UISettings['tapd']>(key: K, value: UISettings['tapd'][K]) =>
    setDraft((prev) => (prev ? {...prev, tapd: {...(prev.tapd ?? TAPD_DEFAULT), [key]: value}} : prev));

  const mailConfigured = Boolean(draft.imap?.host && draft.imap?.user);
  const tapdConfigured = Boolean(tapd.workspace_id && tapd.api_user && (tapd.api_password || settings?.tapd?.api_password));

  const pull = async () => {
    setPulling(true);
    try {
      await pullMail();
    } finally {
      setPulling(false);
    }
  };

  const sync = async () => {
    setSyncing(true);
    try {
      await syncTapd();
    } finally {
      setSyncing(false);
    }
  };

  const copyWebhookUrl = async () => {
    try {
      await navigator.clipboard.writeText(endpoints.tapdWebhookUrl());
      toast.success('Webhook 地址已复制');
    } catch {
      toast.error('复制失败，请手动复制');
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 xl:grid-cols-2">
        {/* TAPD — authoritative source */}
        <Card className="p-5">
          <div className="mb-1 flex items-center justify-between gap-3">
            <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
              <Webhook className="size-4 text-primary" />
              TAPD（主数据源）
            </h3>
            <div className="flex items-center gap-2">
              <Badge variant={tapdConfigured ? 'success' : 'warning'}>
                {tapdConfigured ? '已配置' : '待配置'}
              </Badge>
              <Switch
                checked={tapd.enabled}
                onCheckedChange={(checked) => patchTapd('enabled', checked)}
                aria-label="启用 TAPD 自动同步"
              />
            </div>
          </div>
          <p className="mb-4 text-[11.5px] text-faint">
            工单权威源。通过 Webhook 或定时拉取需求 / 缺陷，按 story / bug ID 幂等建单，完成后回填状态。
          </p>

          <Field label="API 地址" hint="内网用 apiv2.tapd.woa.com">
            <Input
              className="font-mono"
              value={tapd.base_url}
              placeholder="https://api.tapd.cn"
              onChange={(event) => patchTapd('base_url', event.target.value)}
            />
          </Field>
          <div className="-mt-2 mb-3 flex gap-2">
            <button type="button" className="text-[11px] text-primary hover:underline"
              onClick={() => patchTapd('base_url', 'https://api.tapd.cn')}>公网</button>
            <button type="button" className="text-[11px] text-primary hover:underline"
              onClick={() => patchTapd('base_url', 'https://apiv2.tapd.woa.com')}>内网</button>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="项目 ID (workspace_id)">
              <Input
                className="font-mono"
                value={tapd.workspace_id}
                placeholder="10104801"
                onChange={(event) => patchTapd('workspace_id', event.target.value)}
              />
            </Field>
            <Field label="变更人 (current_user)">
              <Input
                className="font-mono"
                value={tapd.current_user}
                placeholder="taskagent"
                onChange={(event) => patchTapd('current_user', event.target.value)}
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="API 账号 (应用 ID)">
              <Input
                className="font-mono"
                value={tapd.api_user}
                placeholder="api_user"
                onChange={(event) => patchTapd('api_user', event.target.value)}
              />
            </Field>
            <Field label="API 密码 (应用秘钥)">
              <Input
                type="password"
                value={tapd.api_password}
                placeholder="留空表示保持已保存值不变"
                onChange={(event) => patchTapd('api_password', event.target.value)}
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="拉取类型">
              <SelectField
                value={tapd.entity_types.includes('bug') ? 'both' : 'story'}
                options={[
                  {value: 'both', label: '需求 + 缺陷'},
                  {value: 'story', label: '仅需求'},
                ]}
                onChange={(value) => patchTapd('entity_types', value === 'both' ? ['story', 'bug'] : ['story'])}
              />
            </Field>
            <Field label="建单后执行">
              <SelectField
                value={tapd.exec_mode}
                options={[
                  {value: 'dry-run', label: 'dry-run（不改动）'},
                  {value: 'apply', label: 'apply（真实执行）'},
                ]}
                onChange={(value) => patchTapd('exec_mode', value as UISettings['tapd']['exec_mode'])}
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="成功后回填状态" hint="英文状态码，如 resolved">
              <Input
                className="font-mono"
                value={tapd.status_on_success}
                placeholder="resolved"
                onChange={(event) => patchTapd('status_on_success', event.target.value)}
              />
            </Field>
            <Field label="失败后回填状态" hint="留空则不回填">
              <Input
                className="font-mono"
                value={tapd.status_on_failure}
                placeholder="留空不回填"
                onChange={(event) => patchTapd('status_on_failure', event.target.value)}
              />
            </Field>
          </div>

          <div className="mt-4 rounded-lg border border-border bg-[#fbfbfd] px-3.5 py-3">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h4 className="text-xs font-semibold text-foreground/80">定时拉取</h4>
                <p className="text-[11px] text-faint">按间隔增量拉取（基于 modified 时间）。</p>
              </div>
              <Field label="间隔（分钟）">
                <Input
                  type="number"
                  min={1}
                  className="w-24"
                  value={tapd.interval_minutes}
                  onChange={(event) => patchTapd('interval_minutes', Number(event.target.value))}
                />
              </Field>
            </div>
            <Field label="Webhook 地址（填到 TAPD 事件订阅）">
              <div className="flex gap-2">
                <Input className="font-mono text-[11px]" readOnly value={endpoints.tapdWebhookUrl()} />
                <Button variant="outline" size="sm" onClick={() => void copyWebhookUrl()}>复制</Button>
              </div>
            </Field>
            <Field label="Webhook 验证密码 (secret，可选)">
              <Input
                className="font-mono"
                value={tapd.webhook_secret}
                placeholder="与 TAPD 后台配置一致"
                onChange={(event) => patchTapd('webhook_secret', event.target.value)}
              />
            </Field>
          </div>

          <Alert variant="warning">
            <AlertDescription>
              需申请 TAPD 应用凭证并授权项目；回填状态需额外开通写权限。凭证明文保存在本机 data/settings.json。
            </AlertDescription>
          </Alert>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button onClick={() => void sync()} disabled={syncing || !tapdConfigured} title="立即从 TAPD 拉取一次">
              <RefreshCw className={syncing ? 'animate-spin' : ''} />
              {syncing ? '同步中…' : '立即同步'}
            </Button>
            <Button variant="outline" onClick={() => void saveSettings(draft)}>
              <Save />
              保存
            </Button>
          </div>
        </Card>

        {/* Email — fallback / notification channel */}
        <Card className="p-5">
          <div className="mb-1 flex items-center justify-between gap-3">
            <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
              <Inbox className="size-4 text-primary" />
              邮件（兜底通知）
            </h3>
            <Badge variant={mailConfigured ? 'success' : 'warning'}>
              {mailConfigured ? '已配置' : '待配置'}
            </Badge>
          </div>
          <p className="mb-4 text-[11.5px] text-faint">
            非结构化、无状态机，仅作临时入口 / 通知提醒；正式工单请以 TAPD 为准。按 message-id 幂等建单。
          </p>

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

          <div className="mt-4 rounded-lg border border-border bg-[#fbfbfd] px-3.5 py-3">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h4 className="text-xs font-semibold text-foreground/80">自动拉取</h4>
                <p className="text-[11px] text-faint">开启后服务按间隔自动收件建单（临时兜底）。</p>
              </div>
              <Switch
                checked={mailPoll.enabled}
                onCheckedChange={(checked) => patchPoll('enabled', checked)}
                aria-label="自动拉取邮件"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="轮询间隔（分钟）">
                <Input
                  type="number"
                  min={1}
                  value={mailPoll.interval_minutes}
                  onChange={(event) => patchPoll('interval_minutes', Number(event.target.value))}
                />
              </Field>
              <Field label="建单后执行">
                <SelectField
                  value={mailPoll.exec_mode}
                  options={[
                    {value: 'dry-run', label: 'dry-run（不改动）'},
                    {value: 'apply', label: 'apply（真实执行）'},
                  ]}
                  onChange={(value) => patchPoll('exec_mode', value as UISettings['mail_poll']['exec_mode'])}
                />
              </Field>
            </div>
            <p className="mt-2 text-[11px] text-faint">开关与间隔保存后即时生效，无需重启。</p>
          </div>

          <Alert variant="warning">
            <AlertDescription>
              凭证以明文保存在本机 data/settings.json，请注意文件权限；密码留空即可保留原值。
            </AlertDescription>
          </Alert>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button onClick={() => void pull()} disabled={pulling || !mailConfigured} title="立即从邮箱拉取新邮件">
              <Inbox className={pulling ? 'animate-pulse' : ''} />
              {pulling ? '拉取中…' : '立即拉取'}
            </Button>
            <Button variant="outline" onClick={() => void saveSettings(draft)}>
              <Save />
              保存
            </Button>
            <Button variant="ghost" onClick={() => void reloadSettings()}>
              <RefreshCw />
              重新载入
            </Button>
          </div>
        </Card>
      </div>

      <Card className="p-5">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <Webhook className="size-4 text-primary" />
          接入说明
        </h3>
        <div className="mt-2 grid gap-3 text-[11.5px] leading-relaxed text-foreground/70 md:grid-cols-3">
          <p>
            <span className="font-semibold text-foreground/85">权威源优先。</span>
            工单以 TAPD / 内部工单系统为准，结构化、有状态机、可回填闭环。Webhook 与邮件只带一个 ID 或提醒，收到后回权威源拉全量详情再干活。
          </p>
          <p>
            <span className="font-semibold text-foreground/85">Webhook 只给 ID。</span>
            TAPD 推送的 payload 不含工单正文，调度器会按其中的 ID 主动回 API 拉取详情，避免只靠通知内容执行。secret 用于校验请求来源。
          </p>
          <p>
            <span className="font-semibold text-foreground/85">邮件仅兜底。</span>
            邮件格式不稳定、无状态机、无法回填，只作临时入口或通知触发；接入 TAPD 后建议将自动拉取保持关闭。
          </p>
        </div>
      </Card>
    </div>
  );
}
