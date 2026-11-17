import * as React from 'react';
import {Inbox, PlugZap, RefreshCw, Save, Webhook} from 'lucide-react';

import {useApp} from '@/app-context';
import {Field, SelectField} from '@/components/select-field';
import {Alert, AlertDescription} from '@/components/ui/alert';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {Switch} from '@/components/ui/switch';
import type {UISettings} from '@/lib/api';

export function DataSourcesPage() {
  const {settings, saveSettings, reloadSettings, pullMail} = useApp();
  const [draft, setDraft] = React.useState<UISettings | null>(settings);
  const [pulling, setPulling] = React.useState(false);

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
  const poll: UISettings['mail_poll'] = draft.mail_poll ?? {
    enabled: false,
    interval_minutes: 5,
    exec_mode: 'dry-run',
  };
  const patchPoll = <K extends keyof UISettings['mail_poll']>(key: K, value: UISettings['mail_poll'][K]) =>
    setDraft((prev) => (prev ? {...prev, mail_poll: {...(prev.mail_poll ?? poll), [key]: value}} : prev));

  const configured = Boolean(draft.imap?.host && draft.imap?.user);

  const pull = async () => {
    setPulling(true);
    try {
      await pullMail();
    } finally {
      setPulling(false);
    }
  };

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card className="p-5">
        <div className="mb-1 flex items-center justify-between gap-3">
          <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
            <Inbox className="size-4 text-primary" />
            邮件（IMAP）
          </h3>
          <Badge variant={configured ? 'success' : 'warning'}>
            {configured ? '已配置' : '待配置'}
          </Badge>
        </div>
        <p className="mb-4 text-[11.5px] text-faint">
          从收件箱拉取新邮件，按 message-id 幂等建单；开启自动拉取后无需人工干预。
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

        <Alert variant="warning">
          <AlertDescription>
            凭证以明文保存在本机 data/settings.json，请注意文件权限；密码留空即可保留原值。
          </AlertDescription>
        </Alert>

        <div className="mt-4 rounded-lg border border-border bg-[#fbfbfd] px-3.5 py-3">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <h4 className="text-xs font-semibold text-foreground/80">自动拉取</h4>
              <p className="text-[11px] text-faint">开启后服务按间隔自动收件建单。</p>
            </div>
            <Switch
              checked={poll.enabled}
              onCheckedChange={(checked) => patchPoll('enabled', checked)}
              aria-label="自动拉取邮件"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="轮询间隔（分钟）">
              <Input
                type="number"
                min={1}
                value={poll.interval_minutes}
                onChange={(event) => patchPoll('interval_minutes', Number(event.target.value))}
              />
            </Field>
            <Field label="建单后执行">
              <SelectField
                value={poll.exec_mode}
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

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button onClick={() => void pull()} disabled={pulling || !configured} title="立即从邮箱拉取新邮件">
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

      <div className="flex flex-col gap-4">
        <Card className="p-5 opacity-70">
          <div className="mb-1 flex items-center justify-between gap-3">
            <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
              <Webhook className="size-4 text-faint" />
              TAPD
            </h3>
            <Badge variant="muted">预留</Badge>
          </div>
          <p className="mb-4 text-[11.5px] text-faint">
            通过 webhook 或开放 API 自动同步需求，接口接缝已留在 sources.py，尚未启用。
          </p>
          <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-[#fbfbfd] px-4 py-8 text-center">
            <PlugZap className="size-6 text-faint" />
            <p className="text-[12px] font-medium text-foreground/60">暂未接入</p>
            <p className="max-w-[280px] text-[11px] leading-relaxed text-faint">
              后续可在此配置 TAPD 工作空间与凭证，实现需求自动建单。
            </p>
          </div>
        </Card>

        <Card className="p-5">
          <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
            <PlugZap className="size-4 text-primary" />
            接入说明
          </h3>
          <p className="mt-2 text-[11.5px] leading-relaxed text-foreground/70">
            外部来源（邮件 / TAPD）统一在此接入，任务自动进入「任务列表」，无需手动新建。
            「新建任务」仅用于手工录入需求或 Bug。
          </p>
          <p className="mt-2 text-[11.5px] leading-relaxed text-foreground/70">
            手动录入的任务不会绑定外部来源；邮件任务按 message-id 去重，重复拉取不会产生重复工单。
          </p>
        </Card>
      </div>
    </div>
  );
}
