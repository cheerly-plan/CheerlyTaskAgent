import * as React from 'react';

import {useApp} from '@/app-context';
import {TapdSourceCard, TAPD_DEFAULT} from '@/components/tapd-source-card';
import {endpoints, type DataSource, type UISettings} from '@/lib/api';
import {toast} from 'sonner';

export function DataSourcesPage() {
  const {settings, saveSettings, mappings, reloadMappings} = useApp();
  const [draft, setDraft] = React.useState<UISettings | null>(settings);
  // 项目↔仓库关联也走草稿：只有点主「保存」才写入 projects.json，
  // 与 settings 草稿保持同一套「保存才生效」的心智。
  const [mappingDraft, setMappingDraft] = React.useState<Record<string, string[]>>({});

  React.useEffect(() => {
    setDraft(settings);
  }, [settings]);

  // UI 与弹窗计算都基于「已落库映射 + 草稿覆盖」的有效视图。
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
    // 切换类型时重建该类型的空配置；目前仅 tapd，切换即重置为 TAPD 默认。
    if (value === 'tapd') {
      patchSource({type: 'tapd', name: 'TAPD', enabled: false, tapd: {...TAPD_DEFAULT}});
    }
  };

  const save = async () => {
    // 取消勾选 / 删除 = 不再拉取该项目；同步解除它和本地仓库的映射，
    // 否则项目映射页会残留一个永远不会再产出工单的绑定。
    const selected = new Set(
      (draft.data_source?.tapd?.workspace_id ?? '')
        .split(',')
        .map((id) => id.trim())
        .filter(Boolean),
    );
    // 最终期望关联 = 草稿覆盖后的值，再剔除不再拉取的项目。与已落库映射比较，
    // 只提交真正有差异的项（草稿里改了又改回来的不写）。
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
