import * as React from 'react';

import {useApp} from '@/app-context';
import {TapdSourceCard, TAPD_DEFAULT} from '@/components/tapd-source-card';
import {type DataSource, type UISettings} from '@/lib/api';

export function DataSourcesPage() {
  const {settings, saveSettings, syncTapd, mappings, reloadMappings} = useApp();
  const [draft, setDraft] = React.useState<UISettings | null>(settings);

  React.useEffect(() => {
    setDraft(settings);
  }, [settings]);

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
            mappings={mappings}
            reloadMappings={reloadMappings}
            onSyncTapd={syncTapd}
          />
        ) : (
          <p className="text-sm text-faint">暂不支持该数据源类型。</p>
        )}
      </div>
    </div>
  );
}

