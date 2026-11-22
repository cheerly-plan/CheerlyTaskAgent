import * as React from 'react';

import {useApp} from '@/app-context';
import {TapdSourceCard, TAPD_DEFAULT} from '@/components/tapd-source-card';
import {endpoints, type DataSource, type UISettings} from '@/lib/api';
import {toast} from 'sonner';

export function DataSourcesPage() {
  const {settings, saveSettings, mappings, reloadMappings} = useApp();
  const [draft, setDraft] = React.useState<UISettings | null>(settings);
  const [mappingDraft, setMappingDraft] = React.useState<Record<string, string[]>>({});

  React.useEffect(() => {
    setDraft(settings);
  }, [settings]);

  const effectiveMappings = React.useMemo(
    () =>
      mappings.map((m) => {
        const ids = mappingDraft[m.id];
        return ids === undefined
          ? m
          : {...m, tapd_workspace_ids: ids, tapd_workspace_id: ids[0] ?? ''};
      }),
    [mappings, mappingDraft],
  );

  const stageMapping = React.useCallback((id: string, ids: string[]) => {
    setMappingDraft((prev) => ({...prev, [id]: ids}));
  }, []);

  if (!draft) {
    return (
      <div className="flex items-center justify-center gap-2 py-20 text-sm text-faint">
        <span className="size-4 animate-spin rounded-full border-2 border-border border-t-primary" />
        正在载入数据源…
      </div>
    );
  }

  const source: DataSource = draft.data_source ?? {type: 'tapd', name: 'TAPD', enabled: false, tapd: {...TAPD_DEFAULT}};

  const patchSource = (next: DataSource) => {
    setDraft((prev) => (prev ? {...prev, data_source: next} : prev));
  };

  const changeType = (value: string) => {
    if (value === 'tapd') {
      patchSource({type: 'tapd', name: 'TAPD', enabled: false, tapd: {...TAPD_DEFAULT}});
    }
  };

  const save = async () => {
    const selected = new Set(
      (draft.data_source?.tapd?.workspace_id ?? '')
        .split(',')
        .map((id) => id.trim())
        .filter(Boolean),
    );
    let changed = 0;
    for (const mapping of mappings) {
      const staged = mappingDraft[mapping.id];
      const ids = staged !== undefined
        ? [...staged]
        : mapping.tapd_workspace_ids?.length
          ? [...mapping.tapd_workspace_ids]
          : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
      const finalIds = ids.filter((id) => selected.has(id));
      const origIds = mapping.tapd_workspace_ids?.length
        ? mapping.tapd_workspace_ids
        : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
      if (finalIds.join(',') === origIds.join(',')) continue;
      await endpoints.updateMapping(mapping.id, {
        tapd_workspace_ids: finalIds,
        tapd_workspace_id: finalIds[0] ?? '',
      });
      changed += 1;
    }
    if (changed) {
      await reloadMappings();
      toast.info(`已同步 ${changed} 项仓库关联的变更`);
    }
    setMappingDraft({});
    await saveSettings(draft);
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4">
        {source.type === 'tapd' ? (
          <TapdSourceCard
            source={source}
            onPatch={patchSource}
            onChangeType={changeType}
            onSave={() => void save()}
            mappings={effectiveMappings}
            onStageMapping={stageMapping}
          />
        ) : (
          <p className="text-sm text-faint">暂不支持该数据源类型。</p>
        )}
      </div>
    </div>
  );
}
