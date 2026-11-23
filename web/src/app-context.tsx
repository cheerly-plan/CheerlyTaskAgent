import * as React from 'react';
import {toast} from 'sonner';


import {ConfirmDialog} from '@/components/dialogs/confirm-dialog';
import {FolderPickerDialog} from '@/components/dialogs/folder-picker-dialog';
import {NewTaskDialog, type ManualTaskDraft} from '@/components/dialogs/new-task-dialog';
import {MappingDialog} from '@/components/dialogs/mapping-dialog';
import {ScanDialog} from '@/components/dialogs/scan-dialog';
import {TapdSyncDialog} from '@/components/dialogs/tapd-sync-dialog';
import type {ConnState, PageKey} from '@/components/app-sidebar';
import type {ActionSelection} from '@/components/task-actions';
import {analyzeReport, type ReportSummary} from '@/lib/report-view';
import {
  endpoints,
  type BackendInfo,
  type ExecMode,
  type ItemAction,
  type MappingDraft,
  type ModelChoice,
  type ProjectMapping,
  type TapdImportSelection,
  type TapdPreviewResult,
  type TapdWorkspace,
  type UISettings,
  type WorkItem,
} from '@/lib/api';

const ITEMS_FULL_LIMIT = 2000;

const HISTORY_PAGE_SIZE = 200;

interface HistoryPageState {
  items: WorkItem[];
  total: number;
  hasMore: boolean;
  loading: boolean;
}

const EMPTY_HISTORY_PAGE: HistoryPageState = {items: [], total: 0, hasMore: false, loading: false};

interface AppContextValue {
  items: WorkItem[];
  itemsTotal: number;
  historyItems: WorkItem[];
  historyTotal: number;
  historyHasMore: boolean;
  historyLoading: boolean;
  loadMoreHistory: () => Promise<void>;
  reloadHistory: () => Promise<void>;
  mappings: ProjectMapping[];
  models: ModelChoice[];
  defaultModel: string;
  backends: BackendInfo[];
  settings: UISettings | null;
  autoRunItems: (ids: string[]) => Promise<void>;
  conn: ConnState;
  connText: string;
  loading: boolean;
  page: PageKey;
  setPage: (page: PageKey) => void;
  reportView: ReportViewState & {
    summary: ReportSummary | null;
    backLabel: string;
    backFrom: PageKey;
  };
  openReport: (id: string) => Promise<void>;
  closeReport: () => void;
  selections: Record<string, ActionSelection>;
  setSelection: (id: string, key: keyof ActionSelection, value: string) => void;
  reloadItems: () => Promise<void>;
  autoMapItems: () => Promise<void>;
  reloadMappings: () => Promise<void>;
  reloadSettings: () => Promise<void>;
  saveSettings: (next: UISettings) => Promise<void>;
  runItemAction: (action: ItemAction, id: string) => Promise<void>;
  mapItemNow: (id: string, repo: string) => Promise<void>;
  openNewTask: () => void;
  syncTapd: (force?: boolean) => Promise<void>;
  openTapdSync: (force?: boolean) => void;
  pickFolder: (apply: (path: string) => void) => void;
  openScan: (path: string) => void;
  openMappingModal: (mapping: ProjectMapping | null) => void;
  toggleMapping: (id: string) => Promise<void>;
  deleteMapping: (id: string) => Promise<void>;
  confirm: (message: string) => Promise<boolean>;
  modelSelectionSupported: (executorId?: string) => boolean;
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

export interface ReportViewState {
  loading: boolean;
  error: string | null;
  item: WorkItem | null;
  markdown: string | null;
  downloadUrl: string | null;
  downloadName: string;
  from: PageKey;
}

const EMPTY_REPORT: ReportViewState = {
  loading: false,
  error: null,
  item: null,
  markdown: null,
  downloadUrl: null,
  downloadName: '',
  from: 'history',
};

const PAGE_LABEL: Record<PageKey, string> = {
  dashboard: '概览',
  tasks: '任务中心',
  history: '历史记录',
  mapping: '项目映射',
  sources: '数据源',
  settings: '系统设置',
  report: '报告',
};

export function AppProvider({
  children,
  page,
  setPage,
}: {
  children: React.ReactNode;
  page: PageKey;
  setPage: (page: PageKey) => void;
}) {
  const [items, setItems] = React.useState<WorkItem[]>([]);
  const [itemsTotal, setItemsTotal] = React.useState(0);
  const [historyPage, setHistoryPage] = React.useState<HistoryPageState>(EMPTY_HISTORY_PAGE);
  const [mappings, setMappings] = React.useState<ProjectMapping[]>([]);
  const [models, setModels] = React.useState<ModelChoice[]>([]);
  const [defaultModel, setDefaultModel] = React.useState('');
  const [backends, setBackends] = React.useState<BackendInfo[]>([]);
  const [settings, setSettings] = React.useState<UISettings | null>(null);
  const [conn, setConn] = React.useState<ConnState>('connecting');
  const [loading, setLoading] = React.useState(true);
  const [selections, setSelections] = React.useState<Record<string, ActionSelection>>({});

  const [newTaskOpen, setNewTaskOpen] = React.useState(false);
  const [tapdSync, setTapdSync] = React.useState<{
    open: boolean;
    force: boolean;
    loading: boolean;
    preview: TapdPreviewResult | null;
  }>({open: false, force: false, loading: false, preview: null});
  const [folderPicker, setFolderPicker] = React.useState<FolderPickerState>({open: false, apply: null});
  const [scan, setScan] = React.useState<{open: boolean; path: string}>({open: false, path: ''});
  const [mappingDialog, setMappingDialog] = React.useState<{open: boolean; editing: ProjectMapping | null}>({
    open: false,
    editing: null,
  });
  const [report, setReport] = React.useState<ReportViewState>(EMPTY_REPORT);
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

  const reloadItems = React.useCallback(async () => {
    try {
      const data = await endpoints.items({limit: ITEMS_FULL_LIMIT});
      setItems(data.items ?? []);
      setItemsTotal(data.total ?? data.items?.length ?? 0);
    } catch (error) {
      toast.error('加载任务失败：' + (error as Error).message);
    }
  }, []);

  const fetchHistoryPage = React.useCallback(async (offset: number) => {
    const data = await endpoints.items({limit: HISTORY_PAGE_SIZE, offset});
    return {items: data.items ?? [], total: data.total ?? 0};
  }, []);

  const historyLoadingRef = React.useRef(false);
  const historyItemsRef = React.useRef<WorkItem[]>([]);
  React.useEffect(() => {
    historyItemsRef.current = historyPage.items;
  }, [historyPage.items]);

  const reloadHistory = React.useCallback(async () => {
    if (historyLoadingRef.current) return;
    historyLoadingRef.current = true;
    setHistoryPage((prev) => ({...prev, loading: true}));
    try {
      const page = await fetchHistoryPage(0);
      setHistoryPage({
        items: page.items,
        total: page.total,
        hasMore: page.items.length > 0 && page.items.length < page.total,
        loading: false,
      });
    } catch (error) {
      setHistoryPage((prev) => ({...prev, loading: false}));
      toast.error('加载历史记录失败：' + (error as Error).message);
    } finally {
      historyLoadingRef.current = false;
    }
  }, [fetchHistoryPage]);

  const loadMoreHistory = React.useCallback(async () => {
    if (historyLoadingRef.current) return;
    historyLoadingRef.current = true;
    setHistoryPage((prev) => ({...prev, loading: true}));
    try {
      const loaded = historyItemsRef.current;
      const page = await fetchHistoryPage(loaded.length);
      if (loaded.length > 0 && page.items.length === 0) {
        setHistoryPage({items: loaded, total: page.total, hasMore: false, loading: false});
        return;
      }
      const merged = [...loaded, ...page.items];
      setHistoryPage({
        items: merged,
        total: page.total,
        hasMore: page.items.length >= HISTORY_PAGE_SIZE && merged.length < page.total,
        loading: false,
      });
    } catch (error) {
      setHistoryPage((prev) => ({...prev, loading: false}));
      toast.error('加载更多历史记录失败：' + (error as Error).message);
    } finally {
      historyLoadingRef.current = false;
    }
  }, [fetchHistoryPage]);

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

  const autoRunItems = React.useCallback(
    async (ids: string[]) => {
      if (!ids.length) return;
      try {
        const result = await endpoints.autoRun(ids);
        await reloadItems();
        if (result.started.length) {
          toast.success(`已启动 ${result.started.length} 个自动执行任务`);
        } else {
          toast.info('没有任务被启动');
        }
        if (result.skipped.length) {
          toast.warning(
            `${result.skipped.length} 个任务被跳过：` +
              result.skipped.map((entry) => entry.reason).join('；'),
          );
        }
      } catch (error) {
        toast.error('启动自动执行失败：' + (error as Error).message);
      }
    },
    [reloadItems],
  );

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
      await Promise.all([reloadMappings(), reloadItems(), reloadHistory(), reloadSettings()]);
    })().finally(() => setLoading(false));
  }, [reloadConfig, reloadItems, reloadHistory, reloadMappings, reloadSettings]);

  React.useEffect(() => {
    const cid = (settings?.tapd?.company_id || '').trim();
    if (!cid || !settings?.tapd?.api_user || tapdProjects.length) return;
    void endpoints.tapdWorkspaces({company_id: cid})
      .then((result) => setTapdProjects(result.projects ?? []))
      .catch(() => undefined);
  }, [settings?.tapd?.company_id, settings?.tapd?.api_user, tapdProjects.length]);

  React.useEffect(() => {
    const source = new EventSource('/api/events');
    source.onopen = () => setConn('live');
    source.onerror = () => setConn('reconnecting');
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    source.onmessage = (event) => {
      if (event.data === '[DONE]') return;
      let payload: {replay?: boolean; type?: string} = {};
      try {
        payload = JSON.parse(event.data) as {replay?: boolean; type?: string};
        if (payload?.replay) return;
      } catch {
      }
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        void reloadItems();
        void reloadHistory();
      }, 120);
    };
    return () => {
      if (refreshTimer) clearTimeout(refreshTimer);
      source.close();
    };
  }, [reloadItems, reloadHistory]);

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
          probedBackends.current.delete(executorId);
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

  const openReport = React.useCallback(
    async (id: string) => {
      const item = items.find((entry) => entry.id === id) ?? null;
      setPage('report');
      setReport((prev) => ({
        ...prev,
        item,
        loading: Boolean(item?.report_path),
        error: null,
        markdown: null,
        downloadUrl: item?.report_path ? endpoints.reportUrl(id) : null,
        downloadName: `${id}.md`,
        from: page,
      }));
      if (!item?.report_path) return;

      try {
        const markdown = await endpoints.report(id);
        setReport((prev) => ({...prev, loading: false, error: null, markdown}));
      } catch (error) {
        const message = (error as Error).message;
        setReport((prev) => ({...prev, loading: false, error: message}));
        toast.error('打开报告失败：' + message);
      }
    },
    [items, page, setPage],
  );

  const closeReport = React.useCallback(() => {
    setReport((prev) => ({...prev, loading: false, error: null}));
    setPage(report.from);
  }, [report.from, setPage]);

  const runItemAction = React.useCallback(
    async (action: ItemAction, id: string) => {
      if (action === 'report') {
        await openReport(id);
        return;
      }

      const item = items.find((entry) => entry.id === id);
      const selection = selections[id] ?? {};
      try {
        if (action === 'exec') {
          const mode = selection.mode || item?.exec_mode || 'dry-run';
          const model = modelSelectionSupported(item?.executor)
            ? selection.model || item?.model || null
            : null;
          if (mode === 'apply') {
            const ok = await confirm(
              'apply 会在隔离 worktree 中真实执行 codebuddy 改代码（不会提交/推送）。确认继续？',
            );
            if (!ok) return;
            await endpoints.runItem(id, {exec_mode: 'apply', approved: true, model});
          } else {
            await endpoints.runItem(id, {exec_mode: 'dry-run', model});
          }
        } else if (action === 'map') {
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
        } else if (action === 'merge') {
          const ok = await confirm(
            '重新尝试把隔离 worktree 的改动原子合并到主仓库工作区（无冲突才写入，不提交）。确认继续？',
          );
          if (!ok) return;
          const merged = await endpoints.mergeItem(id);
          if (merged.merge_state === 'merged') {
            toast.success('已合并到主仓库工作区（未提交）');
          } else {
            const conflicts = merged.result?.sync_conflicts?.length ?? 0;
            toast.warning(
              conflicts
                ? `合并有 ${conflicts} 个文件冲突，未写入主仓；请先处理冲突后重试`
                : '合并未成功，请查看报告',
            );
          }
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
      await reloadHistory();
    },
    [confirm, items, modelSelectionSupported, openReport, reloadItems, reloadHistory, selections],
  );

  const mapItemNow = React.useCallback(
    async (id: string, repo: string) => {
      try {
        await endpoints.mapItem(id, repo);
        await reloadItems();
        toast.success('已映射仓库：' + repo);
      } catch (error) {
        toast.error('映射失败：' + (error as Error).message);
      }
    },
    [reloadItems],
  );

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

  const syncTapd = React.useCallback(async (force = false) => {
    try {
      const result = await endpoints.syncTapd({force});
      await reloadItems();
      if (result.created || result.archived) {
        const parts = [
          result.created ? `新增 ${result.created}` : '',
          result.archived ? `归档 ${result.archived}` : '',
          result.skipped ? `跳过 ${result.skipped} 条重复` : '',
        ].filter(Boolean);
        toast.success(`已从 TAPD 同步：${parts.join('，')}`);
      } else {
        toast.info(`TAPD 暂无新工单${result.skipped ? `（跳过 ${result.skipped} 条重复）` : ''}`);
      }
    } catch (error) {
      toast.error('同步 TAPD 失败：' + (error as Error).message);
    }
  }, [reloadItems]);

  const fetchTapdPreview = React.useCallback(async (force: boolean) => {
    setTapdSync((prev) => ({...prev, loading: true, force}));
    try {
      const result = await endpoints.tapdPreview({force});
      setTapdSync((prev) => ({...prev, loading: false, preview: result}));
    } catch (error) {
      setTapdSync((prev) => ({...prev, loading: false}));
      toast.error('预览 TAPD 失败：' + (error as Error).message);
    }
  }, []);

  const openTapdSync = React.useCallback((force = false) => {
    setTapdSync({open: true, force, loading: true, preview: null});
    void fetchTapdPreview(force);
  }, [fetchTapdPreview]);

  const confirmTapdImport = React.useCallback(async (selections: TapdImportSelection[]) => {
    const cursor = tapdSync.preview?.cursor ?? '';
    try {
      const result = await endpoints.tapdImport({items: selections, cursor});
      const parts = [
        result.created ? `新增 ${result.created}` : '',
        result.archived ? `归档 ${result.archived}` : '',
        result.updated ? `更新 ${result.updated}` : '',
        result.skipped ? `跳过 ${result.skipped}` : '',
      ].filter(Boolean);
      toast.success(`已导入：${parts.join('，') || '0 条'}`);
      setTapdSync((prev) => ({...prev, open: false, preview: null}));
      await reloadItems();
    } catch (error) {
      toast.error('导入 TAPD 工单失败：' + (error as Error).message);
    }
  }, [tapdSync.preview, reloadItems]);

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

  const saveSettings = React.useCallback(
    async (next: UISettings) => {
      const tapd = next.tapd;
      const payload: UISettings = {
        executor: next.executor || 'codebuddy',
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
          auto_pull: tapd.auto_pull === true,
          interval_minutes: Math.max(1, Number(tapd.interval_minutes) || 5),
          exec_mode: tapd.exec_mode === 'apply' ? 'apply' : 'dry-run',
        } : ({} as UISettings['tapd']),
        data_source: next.data_source ? {
          type: (next.data_source.type || 'tapd').trim(),
          name: (next.data_source.name || '').trim(),
          enabled: next.data_source.enabled === true,
          tapd: next.data_source.tapd ? {
            enabled: next.data_source.tapd.enabled === true,
            base_url: (next.data_source.tapd.base_url || 'https://api.tapd.cn').trim(),
            workspace_id: (next.data_source.tapd.workspace_id || '').trim(),
            company_id: (next.data_source.tapd.company_id || '').trim(),
            projects: next.data_source.tapd.projects ?? [],
            api_user: (next.data_source.tapd.api_user || '').trim(),
            api_password: (next.data_source.tapd.api_password || '').trim(),
            webhook_secret: (next.data_source.tapd.webhook_secret || '').trim(),
            entity_types: next.data_source.tapd.entity_types ?? ['story', 'bug'],
            pull_statuses: next.data_source.tapd.pull_statuses ?? [],
            status_on_success: (next.data_source.tapd.status_on_success || 'resolved').trim(),
            status_on_failure: (next.data_source.tapd.status_on_failure || '').trim(),
            current_user: (next.data_source.tapd.current_user || 'taskagent').trim(),
            auto_pull: next.data_source.tapd.auto_pull === true,
            interval_minutes: Math.max(1, Number(next.data_source.tapd.interval_minutes) || 5),
            exec_mode: next.data_source.tapd.exec_mode === 'apply' ? 'apply' : 'dry-run',
          } : ({} as UISettings['tapd']),
        } : ({} as UISettings['data_source']),
      };
      try {
        await endpoints.saveSettings(payload);
        probedBackends.current.clear();
        await reloadSettings();
        await reloadConfig();
        toast.success('设置已保存并已生效');
      } catch (error) {
        toast.error('保存失败：' + (error as Error).message);
      }
    },
    [reloadConfig, reloadSettings],
  );

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

  const reportView = React.useMemo(
    () => ({
      ...report,
      summary: report.markdown ? analyzeReport(report.markdown) : null,
      backLabel: PAGE_LABEL[report.from],
      backFrom: report.from,
    }),
    [report],
  );

  const value: AppContextValue = {
    page,
    setPage,
    reportView,
    openReport,
    closeReport,
    items,
    itemsTotal,
    historyItems: historyPage.items,
    historyTotal: historyPage.total,
    historyHasMore: historyPage.hasMore,
    historyLoading: historyPage.loading,
    loadMoreHistory,
    reloadHistory,
    mappings,
    models,
    defaultModel,
    backends,
    settings,
    autoRunItems,
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
    mapItemNow,
    openNewTask: () => setNewTaskOpen(true),
    syncTapd,
    openTapdSync,
    pickFolder: (apply) => setFolderPicker({open: true, apply}),
    openScan: (path) => setScan({open: true, path}),
    openMappingModal: (mapping) => setMappingDialog({open: true, editing: mapping}),
    toggleMapping,
    deleteMapping,
    confirm,
    modelSelectionSupported,
    ensureBackendProbe,
  };

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

      <TapdSyncDialog
        open={tapdSync.open}
        onOpenChange={(open) => setTapdSync((prev) => ({...prev, open}))}
        force={tapdSync.force}
        loading={tapdSync.loading}
        preview={tapdSync.preview}
        backends={backends}
        models={models}
        defaultExecutor={settings?.executor || ''}
        defaultModel={effectiveDefaultModel}
        defaultExecMode={(settings?.tapd?.exec_mode || settings?.data_source?.tapd?.exec_mode || 'dry-run') as ExecMode}
        onRefresh={() => void fetchTapdPreview(tapdSync.force)}
        onConfirm={confirmTapdImport}
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


      <ConfirmDialog
        open={confirmState.open}
        message={confirmState.message}
        onResolve={resolveConfirm}
      />
    </AppContext.Provider>
  );
}