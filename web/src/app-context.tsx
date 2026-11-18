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
import {defaultModelLabel} from '@/lib/utils';
import {
  endpoints,
  type BackendInfo,
  type ItemAction,
  type MappingDraft,
  type ModelChoice,
  type ProjectMapping,
  type UISettings,
  type WorkItem,
} from '@/lib/api';

interface AppContextValue {
  items: WorkItem[];
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
  reloadMappings: () => Promise<void>;
  reloadSettings: () => Promise<void>;
  saveSettings: (next: UISettings) => Promise<void>;
  runItemAction: (action: ItemAction, id: string) => Promise<void>;
  openNewTask: () => void;
  pullMail: () => Promise<void>;
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
  const [mappings, setMappings] = React.useState<ProjectMapping[]>([]);
  const [models, setModels] = React.useState<ModelChoice[]>([]);
  const [defaultModel, setDefaultModel] = React.useState('');
  const [backends, setBackends] = React.useState<BackendInfo[]>([]);
  const [settings, setSettings] = React.useState<UISettings | null>(null);
  const [conn, setConn] = React.useState<ConnState>('connecting');
  const [connText, setConnText] = React.useState('连接中…');
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
      const data = await endpoints.items();
      setItems(data.items ?? []);
    } catch (error) {
      toast.error('加载任务失败：' + (error as Error).message);
    }
  }, []);

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
      // 密码不回显：保留空串，用户不填即沿用已保存的值
      setSettings({...data, imap: {...(data.imap ?? {}), password: ''}} as UISettings);
    } catch (error) {
      toast.error('加载设置失败：' + (error as Error).message);
    }
  }, []);

  React.useEffect(() => {
    (async () => {
      try {
        const config = await endpoints.config();
        setModels(config.models ?? []);
        setDefaultModel(config.default_model ?? '');
        setBackends(config.backends ?? [{id: 'codebuddy', label: 'CodeBuddy CLI', command: config.executor, enabled: true}]);
        // 执行端显示后端 label（而非裸 id，如 backend-1）。
        const defaultBackend = (config.backends ?? []).find((b) => b.id === config.executor);
        setConnText('执行端 ' + (defaultBackend?.label || config.executor));
        await Promise.all([reloadMappings(), reloadItems(), reloadSettings()]);
      } catch (error) {
        toast.error('加载失败：' + (error as Error).message);
      } finally {
        setLoading(false);
      }
    })();
  }, [reloadItems, reloadMappings, reloadSettings]);

  // SSE：后端有任何变更就只刷新任务列表（与原实现一致）
  React.useEffect(() => {
    const source = new EventSource('/api/events');
    source.onopen = () => {
      setConn('live');
      setConnText('实时同步中');
    };
    source.onerror = () => {
      setConn('reconnecting');
      setConnText('重连中…');
    };
    source.onmessage = (event) => {
      if (event.data !== '[DONE]') void reloadItems();
    };
    return () => source.close();
  }, [reloadItems]);

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
            const modelLine = item.model
              ? '模型：' + item.model
              : '模型：' + defaultModelLabel(backendModels, backend?.default_model);
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

  // ── 新建任务 / 邮件拉取 ───────────────────────────────────────────────────
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

  const pullMail = React.useCallback(async () => {
    try {
      const result = await endpoints.pullMail();
      await reloadItems();
      if (result.created > 0) {
        toast.success(`已从邮箱拉取 ${result.created} 封新邮件建单${result.skipped ? `，跳过 ${result.skipped} 封重复` : ''}`);
      } else {
        toast.info(`邮箱暂无新需求${result.skipped ? `（跳过 ${result.skipped} 封重复）` : ''}`);
      }
    } catch (error) {
      toast.error('拉取邮件失败：' + (error as Error).message);
    }
  }, [reloadItems]);

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
      const imap = next.imap ?? {host: '', port: 993, user: '', password: '', use_ssl: true};
      const poll = next.mail_poll ?? {enabled: false, interval_minutes: 5, exec_mode: 'dry-run'};
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
        imap: {
          host: (imap.host || '').trim(),
          port: Number(imap.port) || 993,
          user: (imap.user || '').trim(),
          password: imap.password || '',
          use_ssl: imap.use_ssl !== false,
        },
        mail_poll: {
          enabled: poll.enabled === true,
          interval_minutes: Math.max(1, Number(poll.interval_minutes) || 5),
          exec_mode: poll.exec_mode === 'apply' ? 'apply' : 'dry-run',
        },
        tapd: tapd ? {
          enabled: tapd.enabled === true,
          base_url: (tapd.base_url || 'https://api.tapd.cn').trim(),
          workspace_id: (tapd.workspace_id || '').trim(),
          api_user: (tapd.api_user || '').trim(),
          api_password: tapd.api_password || '',
          webhook_secret: tapd.webhook_secret || '',
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
        await reloadSettings();
        toast.success('设置已保存（部分参数需重启服务后生效）');
      } catch (error) {
        toast.error('保存失败：' + (error as Error).message);
      }
    },
    [reloadSettings],
  );

  const value: AppContextValue = {
    items,
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
    reloadMappings,
    reloadSettings,
    saveSettings,
    runItemAction,
    openNewTask: () => setNewTaskOpen(true),
    pullMail,
    syncTapd,
    pickFolder: (apply) => setFolderPicker({open: true, apply}),
    openScan: (path) => setScan({open: true, path}),
    openMappingModal: (mapping) => setMappingDialog({open: true, editing: mapping}),
    toggleMapping,
    deleteMapping,
    openReport,
    confirm,
    modelSelectionSupported,
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