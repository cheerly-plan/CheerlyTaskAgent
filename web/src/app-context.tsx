import * as React from 'react';
import {toast} from 'sonner';

import {ConfirmDialog} from '@/components/dialogs/confirm-dialog';
import {FolderPickerDialog} from '@/components/dialogs/folder-picker-dialog';
import {NewTaskDialog, type ManualTaskDraft} from '@/components/dialogs/new-task-dialog';
import {MappingDialog} from '@/components/dialogs/mapping-dialog';
import {ReportDialog} from '@/components/dialogs/report-dialog';
import {ScanDialog} from '@/components/dialogs/scan-dialog';
import type {ConnState} from '@/components/app-header';
import type {ActionSelection} from '@/components/task-actions';
import {esc, renderMarkdown} from '@/lib/markdown';
import {effectiveDefaultModelId, resolvedModelLabel} from '@/lib/utils';
import {
  endpoints,
  type BackendInfo,
  type ItemAction,
  type MappingDraft,
  type ModelChoice,
  type ProjectMapping,
  type TapdWorkspace,
  type UISettings,
  type WorkItem,
} from '@/lib/api';

interface AppContextValue {
  items: WorkItem[];
  /** 存储里的工作项总数（可能多于已加载的 items）。 */
  itemsTotal: number;
  mappings: ProjectMapping[];
  models: ModelChoice[];
  defaultModel: string;
  backends: BackendInfo[];
  settings: UISettings | null;
  conn: ConnState;
  connText: string;
  loading: boolean;
  selections: Record<string, ActionSelection>;
  setSelection: (id: string, key: keyof ActionSelection, value: string) => void;
  reloadItems: () => Promise<void>;
  /** 按 TAPD 项目绑定，给未映射的历史工单补仓库。 */
  autoMapItems: () => Promise<void>;
  reloadMappings: () => Promise<void>;
  reloadSettings: () => Promise<void>;
  saveSettings: (next: UISettings) => Promise<void>;
  runItemAction: (action: ItemAction, id: string) => Promise<void>;
  openNewTask: () => void;
  syncTapd: () => Promise<void>;
  pickFolder: (apply: (path: string) => void) => void;
  openScan: (path: string) => void;
  openMappingModal: (mapping: ProjectMapping | null) => void;
  toggleMapping: (id: string) => Promise<void>;
  deleteMapping: (id: string) => Promise<void>;
  openReport: (id: string) => Promise<void>;
  confirm: (message: string) => Promise<boolean>;
  /** 该后端是否接受按任务指定模型（DSH 之类把模型钉在 profile 里 → false）。 */
  modelSelectionSupported: (executorId?: string) => boolean;
  /**
   * 首次要用某个后端的模型下拉时调用：跑一次真实探测并回写结果。
   * 缓存按后端 id，避免每次下拉都起子进程。
   */
  ensureBackendProbe: (executorId?: string) => void;
}

const AppContext = React.createContext<AppContextValue | null>(null);

export function useApp(): AppContextValue {
  const value = React.useContext(AppContext);
  if (!value) throw new Error('useApp 必须在 <AppProvider> 内使用');
  return value;
}

interface FolderPickerState {
  open: boolean;
  apply: ((path: string) => void) | null;
}

interface ReportState {
  open: boolean;
  title: string;
  html: string;
  downloadUrl: string | null;
  downloadName: string;
}

const EMPTY_REPORT: ReportState = {open: false, title: '', html: '', downloadUrl: null, downloadName: ''};

export function AppProvider({children}: {children: React.ReactNode}) {
  const [items, setItems] = React.useState<WorkItem[]>([]);
  const [itemsTotal, setItemsTotal] = React.useState(0);
  const [mappings, setMappings] = React.useState<ProjectMapping[]>([]);
  const [models, setModels] = React.useState<ModelChoice[]>([]);
  const [defaultModel, setDefaultModel] = React.useState('');
  const [backends, setBackends] = React.useState<BackendInfo[]>([]);
  const [settings, setSettings] = React.useState<UISettings | null>(null);
  const [conn, setConn] = React.useState<ConnState>('connecting');
  const [loading, setLoading] = React.useState(true);
  const [selections, setSelections] = React.useState<Record<string, ActionSelection>>({});

  const [newTaskOpen, setNewTaskOpen] = React.useState(false);
  const [folderPicker, setFolderPicker] = React.useState<FolderPickerState>({open: false, apply: null});
  const [scan, setScan] = React.useState<{open: boolean; path: string}>({open: false, path: ''});
  const [mappingDialog, setMappingDialog] = React.useState<{open: boolean; editing: ProjectMapping | null}>({
    open: false,
    editing: null,
  });
  const [report, setReport] = React.useState<ReportState>(EMPTY_REPORT);
  const [tapdProjects, setTapdProjects] = React.useState<TapdWorkspace[]>([]);
  const [confirmState, setConfirmState] = React.useState({open: false, message: ''});
  const confirmResolver = React.useRef<((value: boolean) => void) | null>(null);

  const confirm = React.useCallback(
    (message: string) =>
      new Promise<boolean>((resolve) => {
        confirmResolver.current = resolve;
        setConfirmState({open: true, message});
      }),
    [],
  );

  const resolveConfirm = React.useCallback((value: boolean) => {
    confirmResolver.current?.(value);
    confirmResolver.current = null;
    setConfirmState({open: false, message: ''});
  }, []);

  // ── 数据加载 ──────────────────────────────────────────────────────────────
  const reloadItems = React.useCallback(async () => {
    try {
      const data = await endpoints.items({limit: 1000});
      setItems(data.items ?? []);
      setItemsTotal(data.total ?? data.items?.length ?? 0);
    } catch (error) {
      toast.error('加载任务失败：' + (error as Error).message);
    }
  }, []);

  const autoMapItems = React.useCallback(async () => {
    try {
      const result = await endpoints.autoMapItems();
      await reloadItems();
      if (result.mapped > 0) {
        toast.success(
          `已自动映射 ${result.mapped} 条` +
            (result.ambiguous ? `，${result.ambiguous} 条项目绑定了多个仓库需人工选择` : ''),
        );
      } else if (result.ambiguous > 0) {
        toast.info(`${result.ambiguous} 条工单所属项目绑定了多个仓库，需手动选择`);
      } else {
        toast.info('没有可自动映射的工单（检查项目映射里的 TAPD 项目绑定）');
      }
    } catch (error) {
      toast.error('自动映射失败：' + (error as Error).message);
    }
  }, [reloadItems]);

  const reloadMappings = React.useCallback(async () => {
    try {
      const data = await endpoints.mappings();
      setMappings(data.mappings ?? []);
    } catch (error) {
      toast.error('加载项目映射失败：' + (error as Error).message);
    }
  }, []);

  const reloadSettings = React.useCallback(async () => {
    try {
      const data = await endpoints.settings();
      setSettings(data as UISettings);
    } catch (error) {
      toast.error('加载设置失败：' + (error as Error).message);
    }
  }, []);

  // CLI / 后端信息来自 /api/config。保存设置后也要重新拉取，否则顶栏执行端
  // 与模型下拉会一直显示保存前的旧后端（包括旧的 CLI 路径与标签）。
  const reloadConfig = React.useCallback(async () => {
    try {
      const config = await endpoints.config();
      setModels(config.models ?? []);
      setDefaultModel(config.default_model ?? '');
      setBackends(
        config.backends ?? [
          {id: 'codebuddy', label: 'CodeBuddy CLI', command: config.executor, enabled: true},
        ],
      );
    } catch (error) {
      toast.error('加载配置失败：' + (error as Error).message);
    }
  }, []);

  React.useEffect(() => {
    (async () => {
      await reloadConfig();
      await Promise.all([reloadMappings(), reloadItems(), reloadSettings()]);
    })().finally(() => setLoading(false));
  }, [reloadConfig, reloadItems, reloadMappings, reloadSettings]);

  // 项目名缓存：设置里没存过时静默拉一次，供映射弹窗显示名称而不是裸 ID。
  React.useEffect(() => {
    const cid = (settings?.tapd?.company_id || '').trim();
    if (!cid || !settings?.tapd?.api_user || tapdProjects.length) return;
    void endpoints.tapdWorkspaces({company_id: cid})
      .then((result) => setTapdProjects(result.projects ?? []))
      .catch(() => undefined);
  }, [settings?.tapd?.company_id, settings?.tapd?.api_user, tapdProjects.length]);

  // SSE：后端有任何变更就只刷新任务列表（与原实现一致）
  React.useEffect(() => {
    const source = new EventSource('/api/events');
    // 连接状态只驱动徽标颜色；文案由下面的 connText 统一派生，避免把
    // 顶栏的「执行端 XXX」覆盖成「实时同步中」。
    source.onopen = () => setConn('live');
    source.onerror = () => setConn('reconnecting');
    source.onmessage = (event) => {
      if (event.data !== '[DONE]') void reloadItems();
    };
    return () => source.close();
  }, [reloadItems]);

  const probedBackends = React.useRef(new Set<string>());
  const ensureBackendProbe = React.useCallback(
    (executorId?: string) => {
      if (!executorId || probedBackends.current.has(executorId)) return;
      probedBackends.current.add(executorId);
      void (async () => {
        try {
          const result = await endpoints.probeBackend(executorId);
          setBackends((prev) =>
            prev.map((backend) =>
              backend.id === executorId
                ? {
                    ...backend,
                    model_selection: result.model_selection,
                    models: result.models?.length ? result.models : backend.models,
                  }
                : backend,
            ),
          );
          if (!result.model_selection) {
            toast.info('该后端无法按任务指定模型，已改为只读展示');
          }
        } catch (error) {
          probedBackends.current.delete(executorId);  // 允许下次重试
          toast.error('探测后端能力失败：' + (error as Error).message);
        }
      })();
    },
    [],
  );

  const modelSelectionSupported = React.useCallback(
    (executorId?: string) => {
      if (!executorId) return true;
      const backend = backends.find((entry) => entry.id === executorId);
      return backend?.model_selection !== false;
    },
    [backends],
  );

  const setSelection = React.useCallback(
    (id: string, key: keyof ActionSelection, value: string) =>
      setSelections((prev) => ({...prev, [id]: {...(prev[id] ?? {}), [key]: value}})),
    [],
  );

  // ── 任务动作 ──────────────────────────────────────────────────────────────
  const openReport = React.useCallback(
    async (id: string) => {
      const item = items.find((entry) => entry.id === id);
      const title = '工单详情 / 报告 · ' + id.slice(-8);
      if (item?.report_path) {
        try {
          const markdown = await endpoints.report(id);
          setReport({
            open: true,
            title,
            html: renderMarkdown(markdown),
            downloadUrl: endpoints.reportUrl(id),
            downloadName: `${id}.md`,
          });
        } catch (error) {
          toast.error('打开报告失败：' + (error as Error).message);
        }
        return;
      }

      const summary = item
        ? (() => {
            const backend = item.executor
              ? backends.find((entry) => entry.id === item.executor)
              : undefined;
            const backendModels = backend?.models?.length ? backend.models : models;
            const modelId = item.model || effectiveDefaultModelId(backendModels, backend?.default_model);
            const modelLine = '模型：' + resolvedModelLabel(backendModels, modelId);
            return [
              '标题：' + item.title,
              '状态：' + item.status,
              '类型：' + item.kind + '　来源：' + item.source,
              '项目：' + (item.repo || '未映射') + '　模式：' + item.exec_mode,
              modelLine,
              item.stage ? '阶段：' + item.stage : '',
              item.error ? '错误：' + item.error : '',
              '',
              '该工单还没有生成报告。执行 dry-run 或 apply 后即可查看完整报告。',
            ]
              .filter((line) => line !== '')
              .join('\n');
          })()
        : '工单不存在或已被删除。';
      setReport({
        open: true,
        title,
        html: `<pre class="md-code">${esc(summary)}</pre>`,
        downloadUrl: null,
        downloadName: '',
      });
    },
    [items, backends, models],
  );

  const runItemAction = React.useCallback(
    async (action: ItemAction, id: string) => {
      if (action === 'report') {
        await openReport(id);
        return;
      }

      const item = items.find((entry) => entry.id === id);
      const selection = selections[id] ?? {};
      try {
        if (action === 'map') {
          const repo = selection.map || item?.repo;
          if (!repo) {
            toast.warning('请先选择仓库');
            return;
          }
          await endpoints.mapItem(id, repo);
        } else if (action === 'dry') {
          const model = modelSelectionSupported(item?.executor)
            ? selection.model || item?.model || null
            : null;
          await endpoints.runItem(id, {exec_mode: 'dry-run', model});
        } else if (action === 'apply') {
          const model = modelSelectionSupported(item?.executor)
            ? selection.model || item?.model || null
            : null;
          const ok = await confirm(
            'apply 会在隔离 worktree 中真实执行 codebuddy 改代码（不会提交/推送）。确认继续？',
          );
          if (!ok) return;
          await endpoints.runItem(id, {exec_mode: 'apply', approved: true, model});
        } else if (action === 'cancel') {
          await endpoints.cancelItem(id);
        } else if (action === 'delete') {
          const ok = await confirm('删除这条工单及其本地记录？此操作不可撤销。');
          if (!ok) return;
          await endpoints.deleteItem(id);
        }
      } catch (error) {
        toast.error('操作失败：' + (error as Error).message);
        return;
      }
      await reloadItems();
    },
    [confirm, items, modelSelectionSupported, openReport, reloadItems, selections],
  );

  // ── 新建任务 ──────────────────────────────────────────────────────────────
  const createManual = React.useCallback(
    async (draft: ManualTaskDraft, mode: 'draft' | 'dry' | 'apply') => {
      const title = draft.title.trim();
      if (!title) {
        toast.warning('请输入标题');
        return;
      }
      if (!draft.repo) {
        toast.warning('请先选择目标项目（项目映射中配置）');
        return;
      }
      if (mode === 'apply') {
        const ok = await confirm(
          `apply 会在 ${draft.repo} 的隔离 worktree 中真实执行所选后端改代码（不提交/推送）。确认继续？`,
        );
        if (!ok) return;
      }
      try {
        await endpoints.createManualTask({
          title,
          description: draft.body,
          kind: draft.kind,
          repo_name: draft.repo,
          exec_mode: mode === 'apply' ? 'apply' : 'dry-run',
          executor: draft.executor || 'codebuddy',
          model: modelSelectionSupported(draft.executor) ? draft.model || null : null,
          run_now: mode !== 'draft',
          approved: mode === 'apply',
        });
        setNewTaskOpen(false);
        await reloadItems();
        toast.success(mode === 'draft' ? '草稿已创建' : '工单已创建并提交执行');
      } catch (error) {
        toast.error('创建失败：' + (error as Error).message);
      }
    },
    [confirm, reloadItems],
  );

  const syncTapd = React.useCallback(async () => {
    try {
      const result = await endpoints.syncTapd();
      await reloadItems();
      if (result.created > 0) {
        toast.success(`已从 TAPD 同步 ${result.created} 条${result.skipped ? `，跳过 ${result.skipped} 条重复` : ''}`);
      } else {
        toast.info(`TAPD 暂无新工单${result.skipped ? `（跳过 ${result.skipped} 条重复）` : ''}`);
      }
    } catch (error) {
      toast.error('同步 TAPD 失败：' + (error as Error).message);
    }
  }, [reloadItems]);

  // ── 项目映射 ──────────────────────────────────────────────────────────────
  const saveMapping = React.useCallback(
    async (draft: MappingDraft) => {
      if (!draft.name) {
        toast.warning('请输入项目名');
        return;
      }
      const editing = mappingDialog.editing;
      try {
        if (editing) {
          await endpoints.updateMapping(editing.id, draft);
        } else {
          await endpoints.createMapping(draft);
        }
        setMappingDialog({open: false, editing: null});
        await reloadMappings();
        toast.success('项目映射已保存');
      } catch (error) {
        toast.error('保存失败：' + (error as Error).message);
      }
    },
    [mappingDialog.editing, reloadMappings],
  );

  const toggleMapping = React.useCallback(
    async (id: string) => {
      const mapping = mappings.find((entry) => entry.id === id);
      if (!mapping) return;
      try {
        await endpoints.updateMapping(id, {enabled: !mapping.enabled});
        await reloadMappings();
      } catch (error) {
        toast.error('操作失败：' + (error as Error).message);
      }
    },
    [mappings, reloadMappings],
  );

  const deleteMapping = React.useCallback(
    async (id: string) => {
      const ok = await confirm('删除这条项目映射？');
      if (!ok) return;
      try {
        await endpoints.deleteMapping(id);
        await reloadMappings();
        toast.success('映射已删除');
      } catch (error) {
        toast.error('删除失败：' + (error as Error).message);
      }
    },
    [confirm, reloadMappings],
  );

  // ── 设置 ──────────────────────────────────────────────────────────────────
  const saveSettings = React.useCallback(
    async (next: UISettings) => {
      const tapd = next.tapd;
      const payload: UISettings = {
        executor: next.executor || 'codebuddy',
        // 扁平字段从 backends 派生，避免旧值覆盖用户新填的 command。
        codebuddy_command: (next.backends?.codebuddy?.command || '').trim(),
        dsh_path: (next.backends?.dsh?.command || '').trim(),
        default_model: next.default_model || '',
        max_concurrent: Math.max(1, Number(next.max_concurrent) || 1),
        auto_sync: next.auto_sync !== false,
        backends: Object.fromEntries(
          Object.entries(next.backends ?? {}).map(([id, cfg]) => [
            id,
            {
              label: (cfg.label || '').trim(),
              enabled: cfg.enabled === true,
              command: (cfg.command || '').trim(),
              model: (cfg.model || '').trim(),
              timeout_s: Math.max(1, Number(cfg.timeout_s) || 600),
              auto_sync: cfg.auto_sync !== false,
            },
          ]),
        ),
        tapd: tapd ? {
          enabled: tapd.enabled === true,
          base_url: (tapd.base_url || 'https://api.tapd.cn').trim(),
          workspace_id: (tapd.workspace_id || '').trim(),
          company_id: (tapd.company_id || '').trim(),
          projects: tapd.projects ?? [],
          api_user: (tapd.api_user || '').trim(),
          api_password: (tapd.api_password || '').trim(),
          webhook_secret: (tapd.webhook_secret || '').trim(),
          entity_types: tapd.entity_types ?? ['story', 'bug'],
          pull_statuses: tapd.pull_statuses ?? [],
          status_on_success: (tapd.status_on_success || 'resolved').trim(),
          status_on_failure: (tapd.status_on_failure || '').trim(),
          current_user: (tapd.current_user || 'taskagent').trim(),
          interval_minutes: Math.max(1, Number(tapd.interval_minutes) || 5),
          exec_mode: tapd.exec_mode === 'apply' ? 'apply' : 'dry-run',
        } : ({} as UISettings['tapd']),
      };
      try {
        await endpoints.saveSettings(payload);
        probedBackends.current.clear();  // 后端配置变了，能力需重新探测
        await reloadSettings();
        await reloadConfig();  // CLI / 后端清单随之刷新，顶栏执行端立即更新
        toast.success('设置已保存并已生效');
      } catch (error) {
        toast.error('保存失败：' + (error as Error).message);
      }
    },
    [reloadConfig, reloadSettings],
  );

  // 顶栏执行端：以刚保存的 settings 为准（含用户新改的后端标签 / CLI），
  // 配置里还没有时再回退到 /api/config 的后端清单，最后才显示裸 id。
  const executorLabel = React.useMemo(() => {
    const executorId = settings?.executor || '';
    const fromSettings = (executorId ? settings?.backends?.[executorId]?.label : '') || '';
    const fromConfig = backends.find((backend) => backend.id === executorId)?.label || '';
    return fromSettings || fromConfig || executorId || '未配置';
  }, [settings, backends]);

  const connText =
    conn === 'live'
      ? `执行端 ${executorLabel}`
      : conn === 'reconnecting'
        ? '重连中…'
        : '连接中…';

  const value: AppContextValue = {
    items,
    itemsTotal,
    mappings,
    models,
    defaultModel,
    backends,
    settings,
    conn,
    connText,
    loading,
    selections,
    setSelection,
    reloadItems,
    autoMapItems,
    reloadMappings,
    reloadSettings,
    saveSettings,
    runItemAction,
    openNewTask: () => setNewTaskOpen(true),
    syncTapd,
    pickFolder: (apply) => setFolderPicker({open: true, apply}),
    openScan: (path) => setScan({open: true, path}),
    openMappingModal: (mapping) => setMappingDialog({open: true, editing: mapping}),
    toggleMapping,
    deleteMapping,
    openReport,
    confirm,
    modelSelectionSupported,
    ensureBackendProbe,
  };

  // 新建任务要回填的默认模型：系统设置里「默认执行后端」真正保存的 model 值。
  // 设置一旦保存，settings 刷新后这里自动跟随，无需额外刷新 config。
  const effectiveDefaultModel = React.useMemo(() => {
    const executorId = settings?.executor || '';
    const backend = executorId ? settings?.backends?.[executorId] : undefined;
    return backend?.model || defaultModel || '';
  }, [settings, defaultModel]);

  return (
    <AppContext.Provider value={value}>
      {children}

      <NewTaskDialog
        open={newTaskOpen}
        onOpenChange={setNewTaskOpen}
        mappings={mappings}
        models={models}
        defaultModel={effectiveDefaultModel}
        backends={backends}
        defaultExecutor={settings?.executor || ''}
        onProbe={(id) => ensureBackendProbe(id)}
        onManualCreate={(draft, mode) => void createManual(draft, mode)}
      />

      <FolderPickerDialog
        open={folderPicker.open}
        onOpenChange={(open) => setFolderPicker((prev) => ({...prev, open}))}
        onPick={(path) => folderPicker.apply?.(path)}
      />

      <ScanDialog
        open={scan.open}
        path={scan.path}
        onOpenChange={(open) => setScan((prev) => ({...prev, open}))}
        onAdded={() => void reloadMappings()}
      />

      <MappingDialog
        open={mappingDialog.open}
        editing={mappingDialog.editing}
        tapdProjects={[...(settings?.tapd?.projects ?? []), ...tapdProjects].filter(
          (project, index, list) => list.findIndex((item) => item.id === project.id) === index,
        )}
        onOpenChange={(open) =>
          setMappingDialog((prev) => ({open, editing: open ? prev.editing : null}))
        }
        onSave={(draft) => void saveMapping(draft)}
        onPickFolder={(apply) => setFolderPicker({open: true, apply})}
      />

      <ReportDialog
        open={report.open}
        onOpenChange={(open) => setReport((prev) => ({...prev, open}))}
        title={report.title}
        html={report.html}
        downloadUrl={report.downloadUrl}
        downloadName={report.downloadName}
      />

      <ConfirmDialog
        open={confirmState.open}
        message={confirmState.message}
        onResolve={resolveConfirm}
      />
    </AppContext.Provider>
  );
}