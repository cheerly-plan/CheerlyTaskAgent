import * as React from 'react';
import {Save, ShieldOff} from 'lucide-react';

import {Button} from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {Input} from '@/components/ui/input';
import {endpoints, type NaMeta, type VerifyRepo} from '@/lib/api';
import {toast} from 'sonner';

interface VerifyNaDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  repo: VerifyRepo | null;
  onSaved: (repos: VerifyRepo[]) => void;
}

const CATEGORIES = ['test', 'lint', 'typecheck', 'build'] as const;
const LABELS: Record<string, string> = {
  test: 'test（测试）',
  lint: 'lint（静态检查）',
  typecheck: 'typecheck（类型检查）',
  build: 'build（构建）',
};

type Draft = Record<string, {reason: string; expires_at: string}>;

const toDraft = (repo: VerifyRepo | null): Draft => {
  const out: Draft = {};
  for (const category of CATEGORIES) {
    const meta = repo?.na_meta?.[category];
    out[category] = {
      reason: meta?.reason ?? '',
      expires_at: (meta?.expires_at ?? '').slice(0, 10),
    };
  }
  return out;
};

export function VerifyNaDialog({open, onOpenChange, repo, onSaved}: VerifyNaDialogProps) {
  const [draft, setDraft] = React.useState<Draft>(() => toDraft(repo));
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    if (open) setDraft(toDraft(repo));
  }, [open, repo]);

  const hasCheck = (category: string) =>
    Boolean(repo?.checks?.some((check) => check.category === category));

  const patch = (category: string, key: 'reason' | 'expires_at', value: string) =>
    setDraft((prev) => ({
      ...prev,
      [category]: {...(prev[category] ?? {reason: '', expires_at: ''}), [key]: value},
    }));

  const save = async () => {
    if (!repo) return;
    const na: Record<string, NaMeta> = {};
    for (const category of CATEGORIES) {
      const entry = draft[category];
      const reason = (entry?.reason ?? '').trim();
      if (!reason || hasCheck(category)) continue;
      na[category] = {reason, expires_at: entry?.expires_at?.trim() || ''};
    }
    setSaving(true);
    try {
      const result = await endpoints.updateVerifyNa(repo.mapping_id, na);
      onSaved(result.repos ?? []);
      toast.success('N/A 豁免已更新');
      onOpenChange(false);
    } catch (error) {
      toast.error('保存 N/A 失败：' + (error as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[600px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldOff className="size-4 text-primary" />
            编辑验证 N/A（{repo?.name ?? '—'}）
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="text-[12px] leading-relaxed text-faint">
            没有对应检查的类别可以显式声明 N/A；填写理由后该类别不再阻断门禁。
            过期后自动失效，需要重新批准。已有检查的类别不接受 N/A。
          </p>
          <div className="flex flex-col gap-3">
            {CATEGORIES.map((category) => {
              const checked = hasCheck(category);
              const meta = repo?.na_meta?.[category];
              return (
                <div
                  key={category}
                  className="rounded-lg border border-border bg-card px-3 py-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[12.5px] font-semibold text-foreground">
                      {LABELS[category] ?? category}
                    </span>
                    {checked ? (
                      <span className="text-[11px] text-faint">已有检查，无需 N/A</span>
                    ) : meta?.approved_at ? (
                      <span className="text-[11px] text-faint">
                        {meta.approved_by || 'user'} · {meta.approved_at.slice(0, 19)}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_150px]">
                    <Input
                      value={draft[category]?.reason ?? ''}
                      disabled={checked}
                      placeholder={checked ? '已有检查' : '填写不适用理由'}
                      onChange={(event) => patch(category, 'reason', event.target.value)}
                    />
                    <Input
                      type="date"
                      value={draft[category]?.expires_at ?? ''}
                      disabled={checked}
                      onChange={(event) => patch(category, 'expires_at', event.target.value)}
                    />
                  </div>
                  {meta?.expired && !checked ? (
                    <p className="mt-1 text-[11px] text-destructive">
                      N/A 已过期，当前不再生效。
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => void save()} disabled={saving || !repo}>
            <Save />
            保存 N/A
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
