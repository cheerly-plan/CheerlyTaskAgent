import * as React from 'react';
import {Check, Loader2, RefreshCw} from 'lucide-react';

import {FloatingRepoSelect} from '@/components/floating-repo-select';
import {Button} from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
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
  companyId: string;
  onCompanyIdChange: (value: string) => void;
  onFetch: (companyId: string) => void;
  credentialsReady: boolean;
  availableCompanies: string[];
  workspaces: TapdWorkspace[];
  loading: boolean;
  selectedWorkspaces: string[];
  onToggleWorkspace: (id: string) => void;
  onSelectAll: () => void;
  onClearSelection: () => void;
  mappings: ProjectMapping[];
  associations: Record<string, string[]>;
  onToggleRepo: (workspaceId: string, mappingId: string) => void;
  onApply: () => void;
}

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

  React.useEffect(() => {
    if (open) {
      setCustomMode(false);
      setCustomValue('');
    }
  }, [open]);

  const companyOptions = React.useMemo(() => {
    const ids = new Set<string>();
    for (const cid of availableCompanies) {
      if (cid.trim()) ids.add(cid.trim());
    }
    if (companyId.trim()) ids.add(companyId.trim());
    return [...ids].map((id) => ({value: id, label: id}));
  }, [availableCompanies, companyId]);

  const effectiveCompanyId = customMode ? customValue.trim() : companyId.trim();
  const enabledMappings = mappings.filter((m) => m.enabled);

  const commitCustom = () => {
    const value = customValue.trim();
    if (!value) return;
    onCompanyIdChange(value);
    setCustomMode(false);
  };

  const repoOptions = enabledMappings.map((m) => ({id: m.id, name: m.name, path: m.path}));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[680px]">
        <DialogHeader>
          <DialogTitle>项目</DialogTitle>
        </DialogHeader>

        <DialogBody>
          {!credentialsReady && (
            <Alert variant="warning">
              <AlertDescription>请先在下方填写并保存 API 账号与秘钥，才能拉取项目。</AlertDescription>
            </Alert>
          )}

          <Field label="企业 ID">
            <div className="flex gap-2">
              <div className="min-w-0 flex-1">
                {customMode ? (
                  <Input
                    className="font-mono"
                    autoFocus
                    value={customValue}
                    onChange={(event) => setCustomValue(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') commitCustom();
                    }}
                  />
                ) : (
                  <SelectField
                    value={companyId}
                    options={[
                      ...companyOptions,
                      {value: CUSTOM_COMPANY, label: '手动输入企业 ID…'},
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
                项目列表
              </p>
              <div className="flex items-center gap-2 text-[11px]">
                <button type="button" className="text-primary hover:underline" onClick={onSelectAll}>全选</button>
                <span className="text-faint">·</span>
                <button type="button" className="text-primary hover:underline" onClick={onClearSelection}>清空</button>
              </div>
            </div>
            {workspaces.length === 0 ? (
              <div className="flex h-24 items-center justify-center rounded-lg border border-dashed border-border bg-[#fbfbfd] px-4 text-center text-[11.5px] text-faint">
                {loading
                  ? '正在拉取…'
                  : effectiveCompanyId
                    ? '没有可添加的新项目'
                    : '先选择企业 ID'}
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
                          <p className="mb-1 text-[11px] font-medium text-faint">
                            管理的本地仓库（{repoIds.length}）
                          </p>
                          <FloatingRepoSelect
                            options={repoOptions}
                            selected={repoIds}
                            onToggle={(mappingId) => onToggleRepo(ws.id, mappingId)}
                            emptyText="暂无本地仓库映射，可先在「项目映射」页添加。"
                            size="sm"
                            maxHeight={200}
                          />
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
