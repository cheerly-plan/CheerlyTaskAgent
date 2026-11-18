import * as React from 'react';
import {Plus, RefreshCw, Save, Webhook} from 'lucide-react';

import {useApp} from '@/app-context';
import {NewTapdProjectDialog} from '@/components/dialogs/new-tapd-project-dialog';
import {PullProjectsDialog} from '@/components/dialogs/pull-projects-dialog';
import {Field, SelectField} from '@/components/select-field';
import {Alert, AlertDescription} from '@/components/ui/alert';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {Switch} from '@/components/ui/switch';
import {endpoints, type TapdWorkspace, type UISettings} from '@/lib/api';
import {toast} from 'sonner';

const TAPD_DEFAULT: UISettings['tapd'] = {
  enabled: false,
  base_url: 'https://api.tapd.cn',
  workspace_id: '',
  company_id: '',
  projects: [],
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
  const {settings, saveSettings, syncTapd, mappings, reloadMappings} = useApp();
  const [draft, setDraft] = React.useState<UISettings | null>(settings);
  const [syncing, setSyncing] = React.useState(false);
  const [workspaces, setWorkspaces] = React.useState<TapdWorkspace[]>([]);
  const [nameCache, setNameCache] = React.useState<TapdWorkspace[]>([]);
  const [loadingWorkspaces, setLoadingWorkspaces] = React.useState(false);
  const [pullOpen, setPullOpen] = React.useState(false);
  const [pullCompanyId, setPullCompanyId] = React.useState('');
  const [selectedWorkspaces, setSelectedWorkspaces] = React.useState<string[]>([]);
  const [repoSelection, setRepoSelection] = React.useState<Record<string, string[]>>({});
  const [newProjectOpen, setNewProjectOpen] = React.useState(false);
  const hydrated = React.useRef(false);

  React.useEffect(() => {
    setDraft(settings);
  }, [settings]);

  // 首次进入时静默拉一次项目名，避免界面只显示项目 ID（不弹提示、不改草稿）。
  React.useEffect(() => {
    if (hydrated.current || pullOpen) return;
    const current = draft?.tapd;
    if (!current?.company_id || !current.api_user || !(current.api_password || settings?.tapd?.api_password)) return;
    hydrated.current = true;
    void fetchWorkspaces(current.company_id, true);
  }, [draft, pullOpen, settings]);

  if (!draft) {
    return (
      <div className="flex items-center justify-center gap-2 py-20 text-sm text-faint">
        <span className="size-4 animate-spin rounded-full border-2 border-border border-t-primary" />
        正在载入数据源…
      </div>
    );
  }

  const tapd: UISettings['tapd'] = draft.tapd ?? TAPD_DEFAULT;
  const patchTapd = <K extends keyof UISettings['tapd']>(key: K, value: UISettings['tapd'][K]) =>
    setDraft((prev) => (prev ? {...prev, tapd: {...(prev.tapd ?? TAPD_DEFAULT), [key]: value}} : prev));

  const tapdCredentialsReady = Boolean(tapd.api_user && (tapd.api_password || settings?.tapd?.api_password));
  const tapdConfigured = Boolean(tapd.workspace_id && tapdCredentialsReady);

  const sync = async () => {
    setSyncing(true);
    try {
      await syncTapd();
    } finally {
      setSyncing(false);
    }
  };

  const selectedIds = (tapd.workspace_id || '').split(',').map((id) => id.trim()).filter(Boolean);
  const selectedIdSet = new Set(selectedIds);
  const boundMappings = mappings.filter((mapping) => {
    const ids = mapping.tapd_workspace_ids?.length
      ? mapping.tapd_workspace_ids
      : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
    return ids.some((id) => selectedIdSet.has(id));
  });

  // 规范化项目缓存，并把「已填的项目 ID」补进去——打开弹窗时即使没拉取也能看到已存项目。
  const normalizeProjects = (items: TapdWorkspace[], fallbackCompany = ''): TapdWorkspace[] => {
    const map = new Map<string, TapdWorkspace>();
    for (const item of items ?? []) {
      const id = (item.id || '').trim();
      if (!id) continue;
      map.set(id, {
        id,
        name: (item.name || '').trim(),
        pretty_name: (item.pretty_name || '').trim(),
        company_id: (item.company_id || fallbackCompany).trim(),
      });
    }
    return [...map.values()];
  };
  const allSavedProjects = normalizeProjects(
    [...(tapd.projects ?? []), ...nameCache],
    tapd.company_id || '',
  );
  for (const id of selectedIds) {
    if (!allSavedProjects.some((p) => p.id === id)) {
      allSavedProjects.push({id, name: id, pretty_name: '', company_id: (tapd.company_id || '').trim()});
    }
  }

  // 弹窗里展示当前企业下的项目（已缓存的先显示，拉取后合并最新结果）。
  const visibleWorkspaces = workspaces.filter(
    (p) => !pullCompanyId || p.company_id === pullCompanyId,
  );

  // 打开弹窗：企业 ID / 已选项目 / 项目缓存都从当前草稿带入，并立即拉取一次最新数据。
  const openPull = () => {
    const cid = (tapd.company_id || '').trim();
    setPullCompanyId(cid);
    setSelectedWorkspaces(selectedIds);
    const initialRepos: Record<string, string[]> = {};
    for (const mapping of mappings) {
      const ids = mapping.tapd_workspace_ids?.length
        ? mapping.tapd_workspace_ids
        : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
      for (const wid of ids) {
        initialRepos[wid] = [...(initialRepos[wid] ?? []), mapping.id];
      }
    }
    setRepoSelection(initialRepos);
    setWorkspaces(allSavedProjects);
    setPullOpen(true);
    if (cid && tapdCredentialsReady) void fetchWorkspaces(cid);
  };

  // 从 TAPD 拉取该企业下的项目列表（只需 company_id，服务端已过滤企业自身）。
  const fetchWorkspaces = async (companyIdOverride?: string, silent = false) => {
    const cid = (companyIdOverride ?? pullCompanyId ?? '').trim();
    if (!cid) {
      if (!silent) toast.warning('请先选择企业 ID');
      return;
    }
    setLoadingWorkspaces(true);
    try {
      const result = await endpoints.tapdWorkspaces({company_id: cid});
      const fresh = normalizeProjects(result.projects ?? [], cid);
      const mergedMap = new Map(workspaces.map((p) => [p.id, p]));
      for (const project of fresh) mergedMap.set(project.id, project);
      const merged = [...mergedMap.values()];
      setWorkspaces(merged);
      setNameCache(merged);
      if (!silent) {
        if (fresh.length) {
          toast.success(`拉取到 ${fresh.length} 个 TAPD 项目`);
        } else {
          toast.info('未拉到项目，请检查企业 ID 是否正确');
        }
      }
    } catch (error) {
      if (!silent) toast.error('拉取项目列表失败：' + (error as Error).message);
    } finally {
      setLoadingWorkspaces(false);
    }
  };

  // 切换企业：立即按新企业拉取项目。
  const changePullCompany = (value: string) => {
    const cid = value.trim();
    setPullCompanyId(cid);
    if (cid && tapdCredentialsReady) void fetchWorkspaces(cid);
  };

  const toggleWorkspace = (id: string) => {
    setSelectedWorkspaces((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  const toggleRepo = (workspaceId: string, mappingId: string) => {
    setRepoSelection((prev) => {
      const current = prev[workspaceId] ?? [];
      return {
        ...prev,
        [workspaceId]: current.includes(mappingId)
          ? current.filter((id) => id !== mappingId)
          : [...current, mappingId],
      };
    });
  };

  // 应用弹窗结果：多个项目 ID 逗号分隔写入 workspace_id，并持久化项目缓存与企业 ID。
  const applyPull = async () => {
    const cid = (pullCompanyId || '').trim();
    if (!cid) {
      toast.warning('请先选择企业 ID');
      return;
    }
    if (!selectedWorkspaces.length) {
      toast.warning('请先选择项目');
      return;
    }
    patchTapd('company_id', cid);
    patchTapd('workspace_id', selectedWorkspaces.join(','));
    patchTapd('projects', workspaces);
    // 按「每个项目各自管理的仓库」写回关联：取消勾选的项目会从对应仓库移除。
    try {
      const selectedSet = new Set(selectedWorkspaces);
      const desiredByMapping = new Map<string, string[]>();
      for (const wid of selectedWorkspaces) {
        for (const mid of repoSelection[wid] ?? []) {
          desiredByMapping.set(mid, [...(desiredByMapping.get(mid) ?? []), wid]);
        }
      }
      let changed = false;
      for (const mapping of mappings) {
        const oldIds = mapping.tapd_workspace_ids?.length
          ? mapping.tapd_workspace_ids
          : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
        const nextIds = [...new Set([
          ...oldIds.filter((id) => !selectedSet.has(id)),
          ...(desiredByMapping.get(mapping.id) ?? []),
        ])];
        if (nextIds.join(',') !== oldIds.join(',')) {
          await endpoints.updateMapping(mapping.id, {
            tapd_workspace_ids: nextIds,
            tapd_workspace_id: nextIds[0] ?? '',
          });
          changed = true;
        }
      }
      if (changed) await reloadMappings();
    } catch (error) {
      toast.error('关联本地仓库失败：' + (error as Error).message);
    }
    setPullOpen(false);
    toast.success(`已填入 ${selectedWorkspaces.length} 个项目，记得点「保存」写入设置`);
  };

  const addNewProject = async (project: {id: string; name: string}, repoIds: string[]) => {
    patchTapd('workspace_id', [...new Set([...selectedIds, project.id])].join(','));
    patchTapd('projects', [
      ...allSavedProjects.filter((item) => item.id !== project.id),
      {
        id: project.id,
        name: project.name,
        pretty_name: '',
        company_id: (tapd.company_id || '').trim(),
      },
    ]);
    // 勾选的本地仓库写入关联（并集，不移除已有项目）。
    if (repoIds.length) {
      try {
        for (const mid of repoIds) {
          const mapping = mappings.find((m) => m.id === mid);
          if (!mapping) continue;
          const ids = mapping.tapd_workspace_ids?.length
            ? [...mapping.tapd_workspace_ids]
            : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
          const union = [...new Set([...ids, project.id])];
          await endpoints.updateMapping(mid, {
            tapd_workspace_ids: union,
            tapd_workspace_id: union[0] ?? '',
          });
        }
        await reloadMappings();
      } catch (error) {
        toast.error('关联本地仓库失败：' + (error as Error).message);
      }
    }
    toast.success(`已新增项目：${project.name}，记得点「保存」`);
  };

  const copyWebhookUrl = async () => {
    try {
      await navigator.clipboard.writeText(endpoints.tapdWebhookUrl());
      toast.success('Webhook 地址已复制');
    } catch {
      toast.error('复制失败，请手动复制');
    }
  };

  const selectedNames = selectedIds.map(
    (id) => allSavedProjects.find((p) => p.id === id)?.name || id,
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4">
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
              placeholder="请输入 TAPD API 地址"
              onChange={(event) => patchTapd('base_url', event.target.value)}
            />
          </Field>
          <div className="-mt-2 mb-3 flex gap-2">
            <button type="button" className="text-[11px] text-primary hover:underline"
              onClick={() => patchTapd('base_url', 'https://api.tapd.cn')}>公网</button>
            <button type="button" className="text-[11px] text-primary hover:underline"
              onClick={() => patchTapd('base_url', 'https://apiv2.tapd.woa.com')}>内网</button>
          </div>

          <Field
            label="项目"
            hint={
              selectedNames.length
                ? `已选 ${selectedNames.length} 个项目 · 选完记得点「保存」`
                : '点右侧「拉取项目」按企业 ID 多选项目，或点「新增」登记项目'
            }
          >
            <div className="flex gap-2">
              <Input
                className="min-w-0 flex-1"
                value={selectedNames.join('、')}
                placeholder="请选择项目"
                readOnly
                title={tapd.workspace_id || '尚未选择项目'}
              />
              <Button variant="outline" onClick={() => setNewProjectOpen(true)} title="输入项目名称和项目 ID，并勾选管理的本地仓库">
                <Plus />
                新增
              </Button>
              <Button variant="outline" onClick={openPull} title="选企业、多选项目并指定各自管理的本地仓库">
                <RefreshCw />
                拉取项目
              </Button>
            </div>
          </Field>
          {boundMappings.length > 0 && (
            <p className="-mt-2 mb-3 text-[11px] text-faint">
              已关联本地仓库：{boundMappings.map((m) => m.name).join('、')}
            </p>
          )}

          <Field label="变更人 (current_user)">
            <Input
              className="font-mono"
              value={tapd.current_user}
              placeholder="请输入变更人"
              onChange={(event) => patchTapd('current_user', event.target.value)}
            />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="API 账号 (应用 ID)">
              <Input
                className="font-mono"
                value={tapd.api_user}
                placeholder="请输入 API 账号"
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
            <Field label="成功后回填状态" hint="请输入英文状态码">
              <Input
                className="font-mono"
                value={tapd.status_on_success}
                placeholder="请输入成功状态码"
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
                placeholder="请输入与 TAPD 后台一致的 secret"
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
      </div>

      <Card className="p-5">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <Webhook className="size-4 text-primary" />
          接入说明
        </h3>
        <div className="mt-2 grid gap-3 text-[11.5px] leading-relaxed text-foreground/70 md:grid-cols-2">
          <p>
            <span className="font-semibold text-foreground/85">权威源。</span>
            TAPD 是唯一外部数据源：需求 / 缺陷在这里维护，有状态机、优先级、责任人，完成后回填状态形成闭环。
          </p>
          <p>
            <span className="font-semibold text-foreground/85">Webhook 只给 ID。</span>
            TAPD 推送的 payload 不含工单正文，调度器会按其中的 ID 主动回 API 拉取详情，避免只靠通知内容执行。secret 用于校验请求来源。
          </p>
        </div>
      </Card>

      <PullProjectsDialog
        open={pullOpen}
        onOpenChange={setPullOpen}
        companyId={pullCompanyId}
        onCompanyIdChange={changePullCompany}
        credentialsReady={tapdCredentialsReady}
        availableCompanies={[...new Set(workspaces.map((p) => p.company_id).filter(Boolean))]}
        workspaces={visibleWorkspaces}
        loading={loadingWorkspaces}
        onFetch={(cid) => void fetchWorkspaces(cid)}
        selectedWorkspaces={selectedWorkspaces}
        onToggleWorkspace={toggleWorkspace}
        onSelectAll={() => setSelectedWorkspaces(visibleWorkspaces.map((ws) => ws.id))}
        onClearSelection={() => setSelectedWorkspaces([])}
        mappings={mappings}
        associations={repoSelection}
        onToggleRepo={toggleRepo}
        onApply={() => void applyPull()}
      />

      <NewTapdProjectDialog
        open={newProjectOpen}
        onOpenChange={setNewProjectOpen}
        mappings={mappings}
        onAdd={(project, repoIds) => void addNewProject(project, repoIds)}
      />
    </div>
  );
}
