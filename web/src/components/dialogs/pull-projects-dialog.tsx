import * as React from 'react';
import {Check, ChevronDown, Loader2, RefreshCw} from 'lucide-react';

import {Button} from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {Alert, AlertDescription} from '@/components/ui/alert';
import {Badge} from '@/components/ui/badge';
import {Input} from '@/components/ui/input';
import {Field, SelectField} from '@/components/select-field';
import type {ProjectMapping, TapdWorkspace} from '@/lib/api';
import {cn} from '@/lib/utils';

const CUSTOM_COMPANY = '__custom_company__';

interface PullProjectsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 当前企业 ID（弹窗内下拉选择，页面不再单独填写）。 */
  companyId: string;
  /** 切换企业：父级会立即按新企业拉取项目。 */
  onCompanyIdChange: (value: string) => void;
  /** 手动/回车拉取，传当前企业 ID。 */
  onFetch: (companyId: string) => void;
  credentialsReady: boolean;
  /** 全部已缓存项目的企业 ID（跨企业），用于企业下拉；项目列表只展示当前企业的。 */
  availableCompanies: string[];
  /** 展示的项目列表：优先已缓存项目，拉取后合并最新结果。 */
  workspaces: TapdWorkspace[];
  loading: boolean;
  /** 多选项目 ID。 */
  selectedWorkspaces: string[];
  onToggleWorkspace: (id: string) => void;
  onSelectAll: () => void;
  onClearSelection: () => void;
  mappings: ProjectMapping[];
  /** 每个 TAPD 项目分别管理哪些本地仓库：{项目ID: [本地映射ID]}。 */
  associations: Record<string, string[]>;
  onToggleRepo: (workspaceId: string, mappingId: string) => void;
  onApply: () => void;
}

/**
 * 「拉取项目」弹窗：
 * - 企业 ID 用下拉（选项从已缓存项目的 company_id 提取），保留「手动输入」兜底；
 * - 项目多选，且每个项目分别勾选它管理的本地仓库——避免「两个多选列表」分不清谁管谁；
 * - 打开弹窗 / 切换企业时父级自动拉取，也可手动刷新；企业自身由后端过滤。
 */
export function PullProjectsDialog({
  open,
  onOpenChange,
  companyId,
  onCompanyIdChange,
  onFetch,
  credentialsReady,
  availableCompanies,
  workspaces,
  loading,
  selectedWorkspaces,
  onToggleWorkspace,
  onSelectAll,
  onClearSelection,
  mappings,
  associations,
  onToggleRepo,
  onApply,
}: PullProjectsDialogProps) {
  const [customMode, setCustomMode] = React.useState(false);
  const [customValue, setCustomValue] = React.useState('');
  const [expandedRepos, setExpandedRepos] = React.useState<Set<string>>(new Set());

  React.useEffect(() => {
    if (open) {
      setCustomMode(false);
      setCustomValue('');
      setExpandedRepos(new Set());
    }
  }, [open]);

  const companyOptions = React.useMemo(() => {
    const ids = new Set<string>();
    for (const cid of availableCompanies) {
      if (cid.trim()) ids.add(cid.trim());
    }
    if (companyId.trim()) ids.add(companyId.trim());
    return [...ids].map((id) => ({value: id, label: id, hint: '来自已记录项目'}));
  }, [availableCompanies, companyId]);

  const effectiveCompanyId = customMode ? customValue.trim() : companyId.trim();
  const enabledMappings = mappings.filter((m) => m.enabled);

  const commitCustom = () => {
    const value = customValue.trim();
    if (!value) return;
    onCompanyIdChange(value);
    setCustomMode(false);
  };

  const toggleRepos = (workspaceId: string) => {
    setExpandedRepos((prev) => {
      const next = new Set(prev);
      if (next.has(workspaceId)) next.delete(workspaceId);
      else next.add(workspaceId);
      return next;
    });
  };

  /** 折叠态摘要：已选仓库名（最多 2 个）+ 数量。 */
  const repoSummary = (repoIds: string[]) => {
    const names = repoIds
      .map((id) => enabledMappings.find((m) => m.id === id)?.name)
      .filter((name): name is string => Boolean(name));
    if (names.length === 0) return '未关联仓库';
    if (names.length <= 2) return names.join('、');
    return `${names.slice(0, 2).join('、')} 等 ${names.length} 个`;
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[680px]">
        <DialogHeader>
          <DialogTitle>项目</DialogTitle>
          <DialogDescription>
            选择企业后自动拉取项目；每个项目单独勾选它管理的本地仓库。应用后写回项目与关联关系。
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
          {!credentialsReady && (
            <Alert variant="warning">
              <AlertDescription>请先在下方填写并保存 API 账号与秘钥，才能拉取项目。</AlertDescription>
            </Alert>
          )}

          <Field label="企业 ID" hint="下拉选项从已记录项目提取；也可手动输入新的企业 ID。">
            <div className="flex gap-2">
              <div className="min-w-0 flex-1">
                {customMode ? (
                  <Input
                    className="font-mono"
                    autoFocus
                    value={customValue}
                    placeholder="请输入企业 ID"
                    onChange={(event) => setCustomValue(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') commitCustom();
                    }}
                  />
                ) : (
                  <SelectField
                    value={companyId}
                    placeholder="请选择企业 ID"
                    options={[
                      ...companyOptions,
                      {value: CUSTOM_COMPANY, label: '手动输入企业 ID…', hint: '拉一个新企业的项目'},
                    ]}
                    onChange={(value) => {
                      if (value === CUSTOM_COMPANY) {
                        setCustomMode(true);
                        setCustomValue('');
                        return;
                      }
                      onCompanyIdChange(value);
                    }}
                  />
                )}
              </div>
              {customMode ? (
                <Button variant="outline" onClick={commitCustom} disabled={!customValue.trim()}>
                  确定
                </Button>
              ) : (
                <Button
                  variant="outline"
                  onClick={() => onFetch(effectiveCompanyId)}
                  disabled={loading || !effectiveCompanyId || !credentialsReady}
                  title="重新拉取最新项目列表"
                >
                  {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                  {loading ? '拉取中…' : '拉取'}
                </Button>
              )}
            </div>
          </Field>

          <div className="mt-1">
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <p className="text-xs font-semibold tracking-wide text-foreground/75">
                项目列表（勾选后为每个项目指定本地仓库）
              </p>
              <div className="flex items-center gap-2 text-[11px]">
                <button type="button" className="text-primary hover:underline" onClick={onSelectAll}>全选</button>
                <span className="text-faint">·</span>
                <button type="button" className="text-primary hover:underline" onClick={onClearSelection}>清空</button>
              </div>
            </div>
            {workspaces.length === 0 ? (
              <div className="flex h-24 items-center justify-center rounded-lg border border-dashed border-border bg-[#fbfbfd] px-4 text-center text-[11.5px] text-faint">
                {loading ? '正在拉取…' : effectiveCompanyId ? '该企业下暂无项目，点「拉取」刷新' : '先选择企业 ID'}
              </div>
            ) : (
              <div className="scrollbar-thin max-h-[min(52vh,420px)] space-y-1.5 overflow-y-auto pr-1">
                {workspaces.map((ws) => {
                  const active = selectedWorkspaces.includes(ws.id);
                  const repoIds = associations[ws.id] ?? [];
                  return (
                    <div
                      key={ws.id}
                      className={cn(
                        'rounded-lg border px-3 py-2 transition-colors',
                        active ? 'border-primary bg-primary/5' : 'border-border hover:bg-secondary/60',
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => onToggleWorkspace(ws.id)}
                        className="flex w-full items-center justify-between gap-3 text-left"
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <span
                            className={cn(
                              'flex size-4 shrink-0 items-center justify-center rounded border',
                              active ? 'border-primary bg-primary text-white' : 'border-border',
                            )}
                          >
                            {active ? <Check className="size-3" /> : null}
                          </span>
                          <span className="min-w-0 truncate text-[13px] text-foreground">
                            {ws.name || ws.pretty_name || ws.id}
                          </span>
                        </span>
                        <Badge variant={active ? 'success' : 'info'}>{ws.id}</Badge>
                      </button>
                      {active && (
                        <div className="mt-2 border-t border-border/70 pt-2">
                          <button
                            type="button"
                            onClick={() => toggleRepos(ws.id)}
                            className="flex w-full items-center gap-2 text-left text-[11px] transition-colors hover:opacity-80"
                          >
                            <span className="shrink-0 font-medium text-faint">
                              管理的本地仓库（{repoIds.length}）
                            </span>
                            <span className={cn('min-w-0 flex-1 truncate', repoIds.length ? 'text-foreground' : 'text-faint')}>
                              {repoSummary(repoIds)}
                            </span>
                            <ChevronDown
                              className={cn('size-3.5 shrink-0 text-faint transition-transform', expandedRepos.has(ws.id) && 'rotate-180')}
                            />
                          </button>
                          {expandedRepos.has(ws.id) && (
                            <div className="scrollbar-thin mt-1.5 max-h-[160px] space-y-0.5 overflow-y-auto">
                              {enabledMappings.length === 0 ? (
                                <p className="px-1 py-1 text-[11px] text-faint">暂无本地仓库映射，可先在「项目映射」页添加。</p>
                              ) : (
                                <div className="grid gap-1 sm:grid-cols-2">
                                  {enabledMappings.map((mapping) => {
                                    const checked = repoIds.includes(mapping.id);
                                    return (
                                      <button
                                        key={mapping.id}
                                        type="button"
                                        onClick={() => onToggleRepo(ws.id, mapping.id)}
                                        title={mapping.path}
                                        className={cn(
                                          'flex min-w-0 items-center gap-1.5 rounded px-1.5 py-1 text-left text-[11.5px] transition-colors',
                                          checked ? 'bg-primary/10 text-foreground' : 'hover:bg-secondary/60',
                                        )}
                                      >
                                        <span
                                          className={cn(
                                            'flex size-3.5 shrink-0 items-center justify-center rounded border',
                                            checked ? 'border-primary bg-primary text-white' : 'border-border',
                                          )}
                                        >
                                          {checked ? <Check className="size-2.5" /> : null}
                                        </span>
                                        <span className="min-w-0 truncate">{mapping.name}</span>
                                      </button>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </DialogBody>

        <DialogFooter>
          <span className="mr-auto text-[11px] text-faint">已选 {selectedWorkspaces.length} 个项目</span>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={onApply} disabled={selectedWorkspaces.length === 0}>
            应用
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
