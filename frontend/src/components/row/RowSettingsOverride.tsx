import { GlobalSettingsDTO, ModelInfo, ProviderInfo, RowDTO, RowSettingsDTO } from '@/api/types';
import { api } from '@/api/client';
import { useQueryClient } from '@tanstack/react-query';
import { effectiveRowSettings, modelForSettings, qualityLabel, qualityValue, resetRowSettings } from '@/lib/capabilities';
import { runAction } from '@/lib/feedback';
import { fieldClass, NumberField, TextSetting } from '@/components/settings/SettingFields';

export function RowSettingsOverride({ row, providers, globalSettings }: {
  row: RowDTO; providers: ProviderInfo[]; globalSettings: GlobalSettingsDTO;
}) {
  const queryClient = useQueryClient();
  const settings = row.settings;
  const effective = effectiveRowSettings(globalSettings, settings);
  const provider = providers.find(item => item.id === effective.provider_id);
  const caps = modelForSettings(providers, effective)?.capabilities;
  const update = async (patch: RowSettingsDTO) => {
    await api.updateRow(row.id, { settings: patch });
    await queryClient.invalidateQueries({ queryKey: ['state'] });
  };
  const selectModel = (model: ModelInfo | undefined, inheritModel = false) => {
    if (!model?.capabilities) return Promise.resolve();
    const capabilities = model.capabilities;
    return update({ ...resetRowSettings, provider_id: inheritModel ? null : model.provider_id, model_id: inheritModel ? null : model.id,
      num_images: Math.min(effective.num_images, capabilities.max_images),
      style_preset: capabilities.supports_style ? effective.style_preset : 'None',
      negative_prompt: capabilities.supports_negative_prompt ? effective.negative_prompt : '',
      random_seed: capabilities.supports_seed ? effective.random_seed : true,
      seed: capabilities.supports_seed ? effective.seed : null, safety: 2,
      thinking_budget: 0, thinking_level: 'high',
      output_format: capabilities.output_formats?.[0] || 'png', background: 'auto',
      size_preset: Object.keys(capabilities.size_presets)[0] || 'Square 1024',
    });
  };
  const changeProvider = (id: string) => !id ? update({ ...resetRowSettings }) : selectModel(providers.find(item => item.id === id)?.models[0]);
  const dimension = (key: 'width' | 'height', value: number | null) => update({
    size_preset: 'Custom', custom_size: { width: effective.custom_size?.width || 1024, height: effective.custom_size?.height || 1024, [key]: value || 1024 },
  });

  return <fieldset disabled={row.status === 'Queued' || row.status === 'Generating'} className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs p-1 disabled:opacity-60">
    <legend className="col-span-2 text-text-muted font-semibold mb-3">Row overrides</legend>
    <div className="space-y-1">
      <label htmlFor={`provider-${row.id}`} className="text-text-muted">Provider</label>
      <select id={`provider-${row.id}`} className={fieldClass} value={settings.provider_id || ''} onChange={event => runAction(() => changeProvider(event.target.value))}>
        <option value="">Global ({globalSettings.provider_id})</option>
        {settings.provider_id && !providers.some(item => item.id === settings.provider_id) && <option>{settings.provider_id}</option>}
        {providers.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
    </div>
    <div className="space-y-1">
      <label htmlFor={`model-${row.id}`} className="text-text-muted">Model</label>
      <select id={`model-${row.id}`} className={fieldClass} value={settings.model_id || ''} onChange={event => runAction(() => selectModel(provider?.models.find(item => item.id === (event.target.value || globalSettings.model_id)), !event.target.value))}>
        {!settings.provider_id && <option value="">Global ({globalSettings.model_id})</option>}
        {settings.model_id && !provider?.models.some(item => item.id === settings.model_id) && <option value={settings.model_id}>{settings.model_id} (unavailable)</option>}
        {provider?.models.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
    </div>
    {!caps && <p role="alert" className="col-span-2 text-amber-200">Choose an available model before generating this row.</p>}
    <div className="space-y-1">
      <label htmlFor={`size-${row.id}`} className="text-text-muted">Dimensions</label>
      <select id={`size-${row.id}`} className={fieldClass} value={settings.custom_size ? 'Custom' : settings.size_preset || ''}
        onChange={event => runAction(() => update({ size_preset: event.target.value || null, custom_size: event.target.value === 'Custom' ? { width: 1024, height: 1024 } : null }))}>
        <option value="">Global</option>
        {settings.size_preset && settings.size_preset !== 'Custom' && !caps?.size_presets[settings.size_preset] && <option value={settings.size_preset}>{settings.size_preset} (unavailable)</option>}
        {Object.keys(caps?.size_presets || {}).map(name => <option key={name}>{name}</option>)}
        {caps?.supports_custom_size && <option>Custom</option>}
      </select>
    </div>
    <div className="space-y-1">
      <label className="text-text-muted">Images per prompt</label>
      <NumberField label="Row image count" min={1} max={caps?.max_images || 1} placeholder={`Global (${globalSettings.num_images})`}
        value={settings.num_images} onCommit={value => update({ num_images: value })} />
    </div>
    {settings.custom_size && <div className="col-span-2 grid grid-cols-2 gap-3">
      <NumberField label="Row width" min={1} value={settings.custom_size.width} onCommit={value => dimension('width', value)} />
      <NumberField label="Row height" min={1} value={settings.custom_size.height} onCommit={value => dimension('height', value)} />
    </div>}
    {caps?.supports_quality && <div className="space-y-1">
      <label htmlFor={`quality-${row.id}`} className="text-text-muted">Quality</label>
      <select id={`quality-${row.id}`} className={fieldClass} value={settings.quality == null ? '' : qualityLabel(settings.quality)}
        onChange={event => runAction(() => update({ quality: event.target.value ? qualityValue(event.target.value) : null }))}>
        <option value="">Global ({qualityLabel(globalSettings.quality)})</option>
        {(caps.quality_options || []).filter(value => ['low', 'medium', 'high'].includes(value)).map(value => <option key={value}>{value}</option>)}
      </select>
    </div>}
    {!!caps?.output_formats?.length && <div className="space-y-1">
      <label htmlFor={`format-${row.id}`} className="text-text-muted">Output format</label>
      <select id={`format-${row.id}`} className={fieldClass} value={settings.output_format || ''}
        onChange={event => runAction(() => update({ output_format: event.target.value || null, ...(event.target.value === 'jpeg' ? { background: 'opaque' } : {}) }))}>
        <option value="">Global ({globalSettings.output_format || 'png'})</option>
        {caps.output_formats.map(format => <option key={format} value={format}>{format.toUpperCase()}</option>)}
      </select>
    </div>}
    {caps?.supports_background && <div className="space-y-1">
      <label htmlFor={`background-${row.id}`} className="text-text-muted">Background</label>
      <select id={`background-${row.id}`} className={fieldClass} value={settings.background || ''} onChange={event => runAction(() => update({ background: event.target.value || null }))}>
        <option value="">Global ({globalSettings.background || 'auto'})</option>
        <option value="auto">Auto</option><option value="opaque">Opaque</option>
        <option value="transparent" disabled={effective.output_format === 'jpeg'}>Transparent</option>
      </select>
    </div>}
    {caps?.supports_style && <div className="space-y-1">
      <label htmlFor={`style-${row.id}`} className="text-text-muted">Style</label>
      <select id={`style-${row.id}`} className={fieldClass} value={settings.style_preset || ''} onChange={event => runAction(() => update({ style_preset: event.target.value || null }))}>
        <option value="">Global</option>
        {['None', 'Photorealistic', 'Illustration', '3D Render'].map(value => <option key={value}>{value}</option>)}
      </select>
    </div>}
    {caps?.supports_seed && <div className="space-y-1">
      <label className="text-text-muted">Seed</label>
      <NumberField label="Row seed" value={settings.seed} min={0} placeholder="Global" onCommit={value => update({ seed: value, random_seed: value == null ? null : false })} />
    </div>}
    {caps?.supports_negative_prompt && <div className="col-span-2 space-y-1">
      <label className="text-text-muted">Avoid (added to prompt)</label>
      <TextSetting label="Row negative prompt" value={settings.negative_prompt ?? globalSettings.negative_prompt}
        onCommit={value => update({ negative_prompt: value })} />
    </div>}
    <button className="col-span-2 mt-2 text-text-muted hover:text-white underline text-left" onClick={() => runAction(() => update(resetRowSettings))}>Reset all overrides</button>
  </fieldset>;
}
