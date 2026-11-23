import * as React from 'react';
import {RefreshCw, Save, Settings2, Webhook} from 'lucide-react';

import {FloatingRepoSelect} from '@/components/floating-repo-select';
import {TapdProjectsDialog} from '@/components/dialogs/tapd-projects-dialog';
import {PullProjectsDialog} from '@/components/dialogs/pull-projects-dialog';
import {Field, SelectField} from '@/components/select-field';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {Switch} from '@/components/ui/switch';
import {
  endpoints,
  type DataSource,
  type ProjectMapping,
  type TapdSettings,
  type TapdWorkspace,
} from '@/lib/api';
import {toast} from 'sonner';

export const TAPD_DEFAULT: TapdSettings = {
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
  auto_pull: false,
  interval_minutes: 5,
  exec_mode: 'dry-run',
};

interface TapdSourceCardProps {
  source: DataSource;
  onPatch: (source: DataSource) => void;
  onChangeType: (type: string) => void;
  onSave: () => void;
  mappings: ProjectMapping[];
  onStageMapping: (mappingId: string, workspaceIds: string[]) => void;
}

const SOURCE_TYPES: {value: string; label: string}[] = [
  {value: 'tapd', label: 'TAPD'},
];

export function TapdSourceCard({
  source,
  onPatch,
  onChangeType,
  onSave,
  mappings,
  onStageMapping,
}: TapdSourceCardProps) {
  const tapd: TapdSettings = source.tapd ?? TAPD_DEFAULT;
  const [workspaces, setWorkspaces] = React.useState<TapdWorkspace[]>(tapd.projects ?? []);
  const [nameCache, setNameCache] = React.useState<TapdWorkspace[]>([]);
  const [loadingWorkspaces, setLoadingWorkspaces] = React.useState(false);
  const [pullOpen, setPullOpen] = React.useState(false);
  const [pullCompanyId, setPullCompanyId] = React.useState('');
  const [selectedWorkspaces, setSelectedWorkspaces] = React.useState<string[]>([]);
  const [repoSelection, setRepoSelection] = React.useState<Record<string, string[]>>({});
  const [projectsOpen, setProjectsOpen] = React.useState(false);
  const hydrated = React.useRef(false);

  const patchTapd = <K extends keyof TapdSettings>(key: K, value: TapdSettings[K]) => {
    onPatch({...source, tapd: {...tapd, [key]: value}});
  };

  const tapdCredentialsReady = Boolean(tapd.api_user && tapd.api_password);

  const selectedIds = (tapd.workspace_id || '').split(',').map((id) => id.trim()).filter(Boolean);

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
  const allSavedProjects = normalizeProjects(tapd.projects ?? [], tapd.company_id || '');
  for (const id of selectedIds) {
    if (!allSavedProjects.some((p) => p.id === id)) {
      const cached = nameCache.find((n) => n.id === id);
      allSavedProjects.push({
        id,
        name: cached?.name || id,
        pretty_name: cached?.pretty_name || '',
        company_id: (tapd.company_id || '').trim(),
      });
    }
  }

  const repoIdsByProject: Record<string, string[]> = {};
  const repoNameByProject: Record<string, string[]> = {};
  for (const mapping of mappings) {
    const ids = mapping.tapd_workspace_ids?.length
      ? mapping.tapd_workspace_ids
      : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
    for (const wid of ids) {
      (repoIdsByProject[wid] ??= []).push(mapping.id);
      (repoNameByProject[wid] ??= []).push(mapping.name);
    }
  }

  React.useEffect(() => {
    if (hydrated.current || pullOpen) return;
    if (!tapd.company_id || !tapd.api_user || !tapd.api_password) return;
    hydrated.current = true;
    void fetchWorkspaces(tapd.company_id, true);
  }, []);

  const visibleWorkspaces = workspaces.filter(
    (p) => !pullCompanyId || p.company_id === pullCompanyId,
  );

  const savedProjectIds = new Set(allSavedProjects.map((p) => p.id));
  const pullCandidates = visibleWorkspaces.filter((p) => !savedProjectIds.has(p.id));

  const openPull = () => {
    const cid = (tapd.company_id || '').trim();
    setPullCompanyId(cid);
    setSelectedWorkspaces([]);
    const initialRepos: Record<string, string[]> = {};
    for (const mapping of mappings) {
      const ids = mapping.tapd_workspace_ids?.length
        ? mapping.tapd_workspace_ids
        : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
      for (const wid of ids) {
        initialRepos[wid] = [...(initialRepos[wid] ?? []), mapping.id];
      }
    }
    setRepoSelection({});
    setWorkspaces(allSavedProjects);
    setPullOpen(true);
    if (cid && tapdCredentialsReady) void fetchWorkspaces(cid);
  };

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

  const applyPull = () => {
    const cid = (pullCompanyId || '').trim();
    if (!cid) {
      toast.warning('请先选择企业 ID');
      return;
    }
    if (!selectedWorkspaces.length) {
      toast.warning('请先选择项目');
      return;
    }
    onPatch({
      ...source,
      tapd: {
        ...tapd,
        company_id: cid,
        workspace_id: [...new Set([...selectedIds, ...selectedWorkspaces])].join(','),
        projects: workspaces,
      },
    });
    const desiredByMapping = new Map<string, string[]>();
    for (const wid of selectedWorkspaces) {
      for (const mid of repoSelection[wid] ?? []) {
        desiredByMapping.set(mid, [...(desiredByMapping.get(mid) ?? []), wid]);
      }
    }
    const selectedWithRepo = new Set([...desiredByMapping.values()].flat());
    for (const mapping of mappings) {
      const oldIds = mapping.tapd_workspace_ids?.length
        ? mapping.tapd_workspace_ids
        : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
      const nextIds = [...new Set([
        ...oldIds.filter((id) => !selectedWithRepo.has(id)),
        ...(desiredByMapping.get(mapping.id) ?? []),
      ])];
      if (nextIds.join(',') !== oldIds.join(',')) onStageMapping(mapping.id, nextIds);
    }
    setPullOpen(false);
    setSelectedWorkspaces([]);
    setRepoSelection({});
    toast.success(`已填入 ${selectedWorkspaces.length} 个项目，点「保存」后生效`);
  };

  const addNewProject = async (project: {id: string; name: string}, repoIds: string[]) => {
    onPatch({
      ...source,
      tapd: {
        ...tapd,
        workspace_id: [...new Set([...selectedIds, project.id])].join(','),
        projects: [
          ...allSavedProjects.filter((item) => item.id !== project.id),
          {id: project.id, name: project.name, pretty_name: '', company_id: (tapd.company_id || '').trim()},
        ],
      },
    });
    for (const mid of repoIds) {
      const mapping = mappings.find((m) => m.id === mid);
      if (!mapping) continue;
      const ids = mapping.tapd_workspace_ids?.length
        ? [...mapping.tapd_workspace_ids]
        : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
      onStageMapping(mid, [...new Set([...ids, project.id])]);
    }
    toast.success(`已新增项目：${project.name}，点「保存」后生效`);
  };

  const toggleSelectedProject = (projectId: string) => {
    const next = selectedIds.includes(projectId)
      ? selectedIds.filter((id) => id !== projectId)
      : [...selectedIds, projectId];
    patchTapd('workspace_id', next.join(','));
  };

  const updateProject = (
    projectId: string,
    patch: {name: string; repoIds: string[]},
  ) => {
    onPatch({
      ...source,
      tapd: {
        ...tapd,
        projects: allSavedProjects.map((project) =>
          project.id === projectId ? {...project, name: patch.name} : project,
        ),
      },
    });
    const desired = new Set(patch.repoIds);
    for (const mapping of mappings) {
      const ids = mapping.tapd_workspace_ids?.length
        ? mapping.tapd_workspace_ids
        : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
      const bound = ids.includes(projectId);
      const shouldBind = desired.has(mapping.id);
      if (bound === shouldBind) continue;
      const nextIds = shouldBind
        ? [...ids, projectId]
        : ids.filter((id) => id !== projectId);
      onStageMapping(mapping.id, nextIds);
    }
    toast.success(`已更新项目：${patch.name}，点「保存」后生效`);
  };

  const removeProject = (projectId: string) => {
    const nextIds = selectedIds.filter((id) => id !== projectId);
    const nextTapd: TapdSettings = {
      ...tapd,
      projects: allSavedProjects.filter((item) => item.id !== projectId),
      workspace_id: nextIds.join(','),
    };
    setNameCache((prev) => prev.filter((item) => item.id !== projectId));
    setWorkspaces((prev) => prev.filter((item) => item.id !== projectId));
    onPatch({...source, tapd: nextTapd});
    toast.success('已移除项目，点「保存」后生效');
  };

  return (
    <Card className="p-5">
      <div className="mb-1 flex items-center gap-2.5">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <Webhook className="size-4 text-primary" />
          <SelectField
          value={source.type}
          options={SOURCE_TYPES}
          onChange={onChangeType}
          className="w-40"
        />
        </h3>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={onSave}>
            <Save />
            保存
          </Button>
        </div>
      </div>

      <Field label="API 地址">
        <Input
          className="font-mono"
          value={tapd.base_url}
          onChange={(event) => patchTapd('base_url', event.target.value)}
        />
      </Field>

      <div className="grid grid-cols-2 gap-4">
        <Field label="API 账号 (应用 ID)">
          <Input
            className="font-mono"
            value={tapd.api_user}
            onChange={(event) => patchTapd('api_user', event.target.value)}
          />
        </Field>
        <Field label="API 密码 (应用秘钥)">
          <Input
            type="password"
            value={tapd.api_password}
            onChange={(event) => patchTapd('api_password', event.target.value)}
          />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <Field label="项目">
          <div className="flex gap-2 items-center">
            <FloatingRepoSelect
              options={allSavedProjects.map((p) => ({
                id: p.id,
                name: p.name || p.pretty_name || p.id,
                trailing: repoNameByProject[p.id]?.length
                  ? repoNameByProject[p.id].join('、')
                  : '未关联仓库',
              }))}
              selected={selectedIds}
              onToggle={toggleSelectedProject}
              onRemove={(id) => void removeProject(id)}
              searchable
              onSelectAll={() =>
                patchTapd('workspace_id', allSavedProjects.map((p) => p.id).join(','))
              }
              onClear={() => patchTapd('workspace_id', '')}
            />
            <Button
              variant="outline"
              size="sm"
              onClick={() => setProjectsOpen(true)}
            >
              <Settings2 />
              管理
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={openPull}
            >
              <RefreshCw />
              拉取
            </Button>
          </div>
        </Field>
        <Field label="变更人 (current_user)">
          <Input
            className="font-mono"
            value={tapd.current_user}
            onChange={(event) => patchTapd('current_user', event.target.value)}
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
            onChange={(value) => patchTapd('exec_mode', value as TapdSettings['exec_mode'])}
          />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <Field label="成功后回填状态">
          <Input
            className="font-mono"
            value={tapd.status_on_success}
            onChange={(event) => patchTapd('status_on_success', event.target.value)}
          />
        </Field>
        <Field label="失败后回填状态">
          <Input
            className="font-mono"
            value={tapd.status_on_failure}
            onChange={(event) => patchTapd('status_on_failure', event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center gap-4 rounded-lg border border-border bg-[#fbfbfd] px-3.5 py-3">
        <div className="flex items-center gap-2.5">
          <Switch
            checked={tapd.auto_pull}
            onCheckedChange={(checked) => patchTapd('auto_pull', checked)}
            aria-label="启用定时拉取"
          />
          <div>
            <h4 className="text-xs font-semibold text-foreground/80">定时拉取</h4>
            <p className="mt-0.5 text-[11px] text-faint">拉到的新工单会直接自动执行，结果进「我的待办」</p>
          </div>
        </div>
        <Field label="间隔（分钟）" className="mb-0">
          <Input
            type="number"
            min={1}
            className="w-24"
            value={tapd.interval_minutes}
            onChange={(event) => patchTapd('interval_minutes', Number(event.target.value))}
          />
        </Field>
      </div>

      <PullProjectsDialog
        open={pullOpen}
        onOpenChange={setPullOpen}
        companyId={pullCompanyId}
        onCompanyIdChange={changePullCompany}
        credentialsReady={tapdCredentialsReady}
        availableCompanies={[...new Set(workspaces.map((p) => p.company_id).filter(Boolean))]}
        workspaces={pullCandidates}
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

      <TapdProjectsDialog
        open={projectsOpen}
        onOpenChange={setProjectsOpen}
        mappings={mappings}
        projects={allSavedProjects}
        reposByProject={repoIdsByProject}
        onAdd={(project, repoIds) => addNewProject(project, repoIds)}
        onUpdate={(projectId, patch) => updateProject(projectId, patch)}
      />
    </Card>
  );
}
