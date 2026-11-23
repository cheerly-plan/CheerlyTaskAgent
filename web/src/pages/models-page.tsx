import * as React from 'react';
import {Bot, Plus, RefreshCw, Save, Trash2} from 'lucide-react';

import {useApp} from '@/app-context';
import {Field, SelectField, type SelectFieldOption} from '@/components/select-field';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {Switch} from '@/components/ui/switch';
import type {ModelConfig} from '@/lib/api';

const EMPTY_MODEL: ModelConfig = {
  id: '',
  label: '',
  provider: '',
  base_url: '',
  api_key: '',
  enabled: true,
};

export function ModelsPage() {
  const {settings, saveSettings, reloadSettings} = useApp();
  const [draft, setDraft] = React.useState<ModelConfig[]>(settings?.models ?? []);
  const [agentModel, setAgentModel] = React.useState(settings?.agent_model ?? '');

  React.useEffect(() => {
    setDraft(settings?.models ?? []);
    setAgentModel(settings?.agent_model ?? '');
  }, [settings]);

  if (!settings) {
    return (
      <div className="flex items-center justify-center gap-2 py-20 text-sm text-faint">
        <span className="size-4 animate-spin rounded-full border-2 border-border border-t-primary" />
        正在载入模型配置…
      </div>
    );
  }

  const patch = <K extends keyof ModelConfig>(index: number, key: K, value: ModelConfig[K]) =>
    setDraft((prev) => prev.map((model, i) => (i === index ? {...model, [key]: value} : model)));
  const add = () => setDraft((prev) => [...prev, {...EMPTY_MODEL}]);
  const remove = (index: number) => {
    const removed = draft[index];
    setDraft((prev) => prev.filter((_, i) => i !== index));
    if (removed?.id && removed.id === agentModel) setAgentModel('');
  };

  // 下拉里只列「已启用且有 id」的模型；agent_model 为空时后端回退到第一个。
  const agentOptions: SelectFieldOption[] = draft
    .filter((model) => model.enabled !== false && model.id.trim())
    .map((model) => ({
      value: model.id,
      label: `${model.label || model.id}${model.provider ? ` · ${model.provider}` : ''}`,
    }));
  if (agentOptions.length === 0) {
    agentOptions.push({value: '', label: '未配置模型', disabled: true});
  }
  const agentValue = agentOptions.some((option) => option.value === agentModel)
    ? agentModel
    : agentOptions[0]?.value ?? '';

  const save = () => void saveSettings({...settings, models: draft, agent_model: agentValue});

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <Card className="flex shrink-0 flex-wrap items-center justify-between gap-3 px-5 py-4">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <Bot className="size-4 text-primary" />
          模型配置
        </h3>
        <div className="flex flex-wrap items-center gap-2">
          <SelectField
            id="agent-model"
            size="sm"
            className="w-[220px]"
            value={agentValue}
            onChange={setAgentModel}
            options={agentOptions}
            disabled={agentOptions.length === 0 || agentOptions[0]?.disabled}
          />
          <Button variant="outline" size="sm" onClick={add}>
            <Plus />
            添加模型
          </Button>
        </div>
      </Card>

      {draft.length === 0 ? (
        <Card className="flex min-h-[160px] items-center justify-center text-sm text-faint">
          还没有配置模型。
        </Card>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
          {draft.map((model, index) => (
            <Card key={index} className="shrink-0 p-4">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Switch
                    checked={model.enabled}
                    onCheckedChange={(checked) => patch(index, 'enabled', checked)}
                    aria-label={`${model.enabled ? '停用' : '启用'} ${model.label || model.id || '模型'}`}
                  />
                  <span className="text-[13px] font-semibold text-foreground">
                    {model.label || model.id || `新模型 ${index + 1}`}
                  </span>
                </div>
                <Button
                  variant="danger"
                  size="icon-sm"
                  onClick={() => remove(index)}
                  aria-label={`删除模型 ${model.label || model.id || index + 1}`}
                >
                  <Trash2 />
                </Button>
              </div>
              <div className="grid gap-x-4 md:grid-cols-2">
                <Field label="名称" className="mb-3">
                  <Input
                    value={model.label}
                    placeholder="例如 DeepSeek Chat"
                    onChange={(event) => patch(index, 'label', event.target.value)}
                  />
                </Field>
                <Field label="模型 ID" className="mb-3">
                  <Input
                    className="font-mono"
                    value={model.id}
                    placeholder="例如 deepseek-chat"
                    onChange={(event) => patch(index, 'id', event.target.value)}
                  />
                </Field>
                <Field label="供应商" className="mb-3">
                  <Input
                    value={model.provider}
                    placeholder="例如 DeepSeek"
                    onChange={(event) => patch(index, 'provider', event.target.value)}
                  />
                </Field>
                <Field label="接口地址（Base URL）" className="mb-3">
                  <Input
                    className="font-mono"
                    value={model.base_url}
                    placeholder="https://api.example.com/v1"
                    onChange={(event) => patch(index, 'base_url', event.target.value)}
                  />
                </Field>
                <Field label="API Key" className="mb-0 md:col-span-2">
                  <Input
                    type="password"
                    autoComplete="new-password"
                    value={model.api_key}
                    placeholder={model.api_key ? '留空保持原值' : ''}
                    onChange={(event) => patch(index, 'api_key', event.target.value)}
                  />
                </Field>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Card className="flex shrink-0 flex-wrap items-center gap-2 p-4">
        <Button size="sm" onClick={save}>
          <Save />
          保存设置
        </Button>
        <Button size="sm" variant="outline" onClick={() => void reloadSettings()}>
          <RefreshCw />
          重新载入
        </Button>
      </Card>
    </div>
  );
}