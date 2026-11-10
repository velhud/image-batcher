import { BackendState, GlobalSettingsDTO } from '@/api/types';
import { api } from '@/api/client';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/common/Button';
import { modelForSettings, qualityLabel, qualityValue, resetRowSettings } from '@/lib/capabilities';
import { runAction } from '@/lib/feedback';
import { fieldClass, NumberField, TextSetting } from './SettingFields';

export function GenerationSettingsPanel({ state }: { state: BackendState }) {
  const queryClient = useQueryClient();
  const gs = state.session.global_settings;
  const model = modelForSettings(state.providers, gs);
  const caps = model?.capabilities;
  const sizes = caps?.size_presets || {};
  const selectedRows = state.session.rows.filter(row => row.selected);

  const update = async (patch: Partial<GlobalSettingsDTO>) => {
    await api.updateGlobalSettings(patch);
    await queryClient.invalidateQueries({ queryKey: ['state'] });
  };
  const applyToSelected = async () => {
    for (const row of selectedRows) await api.updateRow(row.id, { settings: resetRowSettings });
    await queryClient.invalidateQueries({ queryKey: ['state'] });
  };
  const dimension = (key: 'width' | 'height', value: number | null) => update({
    size_preset: 'Custom', custom_size: { width: gs.custom_size?.width || 1024, height: gs.custom_size?.height || 1024, [key]: value || 1024 },
  });

  return (
    <div className="space-y-5 text-sm pb-6">
      {!model && <p role="alert" className="text-amber-200 text-xs">The saved model is unavailable. Choose a model in the header before generating.</p>}
      <div className="space-y-2">
        <label htmlFor="global-size" className="text-text-muted text-xs uppercase font-semibold">Image dimensions</label>
        <select id="global-size" value={gs.custom_size ? 'Custom' : gs.size_preset} disabled={!caps}
          onChange={e => runAction(() => update({ size_preset: e.target.value, custom_size: e.target.value === 'Custom' ? { width: 1024, height: 1024 } : null }))} className={fieldClass}>
          {!sizes[gs.size_preset] && !gs.custom_size && <option value={gs.size_preset}>{gs.size_preset} (unavailable)</option>}
          {Object.entries(sizes).map(([name, size]) => <option key={name} value={name}>{name} ({size.width}×{size.height})</option>)}
          {caps?.supports_custom_size && <option value="Custom">Custom</option>}
        </select>
        {gs.custom_size && <div className="grid grid-cols-2 gap-2">
          <NumberField label="Width" value={gs.custom_size.width} min={1} onCommit={value => dimension('width', value)} />
          <NumberField label="Height" value={gs.custom_size.height} min={1} onCommit={value => dimension('height', value)} />
        </div>}
        {gs.custom_size && <p className="text-[11px] text-text-muted">Preflight checks dimensions against the selected model.</p>}
      </div>

      <div className="space-y-2">
        <label className="text-text-muted text-xs uppercase font-semibold">Images per prompt</label>
        <NumberField label="Images per prompt" value={gs.num_images} min={1} max={caps?.max_images || 1} disabled={!caps}
          onCommit={value => update({ num_images: value || 1 })} />
      </div>

      {caps?.supports_style && <div className="space-y-2">
        <label htmlFor="global-style" className="text-text-muted text-xs uppercase font-semibold">Style</label>
        <select id="global-style" value={gs.style_preset} onChange={e => runAction(() => update({ style_preset: e.target.value }))} className={fieldClass}>
          {['None', 'Photorealistic', 'Illustration', '3D Render'].map(style => <option key={style}>{style}</option>)}
        </select>
      </div>}

      {caps?.supports_negative_prompt && <div className="space-y-2">
        <label className="text-text-muted text-xs uppercase font-semibold">{gs.provider_id === 'google' ? 'Avoid (added to prompt)' : 'Negative prompt'}</label>
        <TextSetting label="Negative prompt" value={gs.negative_prompt || ''} onCommit={value => update({ negative_prompt: value })} />
      </div>}

      {caps?.supports_seed && <div className="space-y-2 border-t border-white/5 pt-3">
        <label className="flex items-center gap-2 text-xs text-text-muted"><input type="checkbox" checked={gs.random_seed}
          onChange={e => runAction(() => update({ random_seed: e.target.checked }))} /> Random seed</label>
        {!gs.random_seed && <NumberField label="Seed" value={gs.seed ?? 0} min={0} onCommit={value => update({ seed: value ?? 0 })} />}
        <label className="flex items-center gap-2 text-xs text-text-muted"><input type="checkbox" checked={gs.regen_use_same_seed}
          onChange={e => runAction(() => update({ regen_use_same_seed: e.target.checked }))} /> Reuse seed when regenerating</label>
      </div>}

      {caps?.supports_quality && <div className="space-y-2">
        <label htmlFor="global-quality" className="text-text-muted text-xs uppercase font-semibold">Quality</label>
        <select id="global-quality" className={fieldClass} value={qualityLabel(gs.quality)}
          onChange={e => runAction(() => update({ quality: qualityValue(e.target.value) }))}>
          {(caps.quality_options || ['low', 'medium', 'high']).filter(value => ['low', 'medium', 'high'].includes(value)).map(value => <option key={value}>{value}</option>)}
        </select>
      </div>}

      {!!caps?.output_formats?.length && <div className="space-y-2">
        <label htmlFor="global-format" className="text-text-muted text-xs uppercase font-semibold">Output format</label>
        <select id="global-format" className={fieldClass} value={gs.output_format || 'png'}
          onChange={e => runAction(() => update({ output_format: e.target.value, ...(e.target.value === 'jpeg' ? { background: 'opaque' } : {}) }))}>
          {caps.output_formats.map(format => <option key={format} value={format}>{format.toUpperCase()}</option>)}
        </select>
      </div>}
      {caps?.supports_background && <div className="space-y-2">
        <label htmlFor="global-background" className="text-text-muted text-xs uppercase font-semibold">Background</label>
        <select id="global-background" className={fieldClass} value={gs.background || 'auto'} onChange={e => runAction(() => update({ background: e.target.value }))}>
          <option value="auto">Auto</option><option value="opaque">Opaque</option>
          <option value="transparent" disabled={gs.output_format === 'jpeg'}>Transparent (PNG / WebP)</option>
        </select>
      </div>}

      <div className="space-y-2">
        <p className="text-text-muted text-xs uppercase font-semibold">Shared prompt instructions</p>
        <TextSetting label="Shared prompt instructions" rows={4} value={gs.prompt_wrapper || ''} onCommit={value => update({ prompt_wrapper: value })} />
      </div>
      <div className="border-t border-white/5 pt-3 text-xs text-text-muted">
        {caps?.supports_attachments ? `Reference images: up to ${caps.max_attachments} per row. Add them with the paperclip.` : 'This model does not accept reference images.'}
      </div>
      <div className="space-y-2">
        <label htmlFor="generation-behavior" className="text-text-muted text-xs uppercase font-semibold">When regenerating</label>
        <select id="generation-behavior" value={gs.generate_behavior} onChange={e => runAction(() => update({ generate_behavior: e.target.value }))} className={fieldClass}>
          <option value="keep">Keep previous images</option><option value="replace">Replace after a successful run</option>
        </select>
      </div>
      <div className="pt-3">
        <Button onClick={() => runAction(applyToSelected)} className="w-full" variant="secondary" size="sm" disabled={!selectedRows.length || selectedRows.some(row => row.status === 'Queued' || row.status === 'Generating')}>
          Apply Defaults to Selected Rows
        </Button>
        <p className="text-[10px] text-text-muted mt-2 text-center">Clears all overrides, including format and background.</p>
      </div>
    </div>
  );
}
