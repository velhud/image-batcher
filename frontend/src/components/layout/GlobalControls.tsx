import { BackendState, ModelInfo } from '@/api/types';
import { api } from '@/api/client';
import { useQueryClient } from '@tanstack/react-query';
import { ChevronDown } from 'lucide-react';
import { runAction } from '@/lib/feedback';

interface GlobalControlsProps {
  state: BackendState;
}

export function GlobalControls({ state }: GlobalControlsProps) {
  const queryClient = useQueryClient();
  const settings = state.session.global_settings;

  // Find current provider info to get models
  const currentProvider = state.providers.find(p => p.id === settings.provider_id);

  const selectModel = async (model: ModelInfo | undefined) => {
    if (!model) return;
    const caps = model.capabilities;
    await api.updateGlobalSettings({
      provider_id: model.provider_id, model_id: model.id,
      num_images: Math.min(settings.num_images, caps?.max_images || 1),
      style_preset: caps?.supports_style ? settings.style_preset : 'None',
      negative_prompt: caps?.supports_negative_prompt ? settings.negative_prompt : '',
      seed: caps?.supports_seed ? settings.seed : null,
      random_seed: caps?.supports_seed ? settings.random_seed : true,
      thinking_budget: 0, thinking_level: 'high',
      safety: 2, output_format: caps?.output_formats?.[0] || 'png', background: 'auto',
      custom_size: null, size_preset: Object.keys(caps?.size_presets || {})[0] || 'Square 1024',
    });
    await queryClient.invalidateQueries({ queryKey: ['state'] });
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* Provider Selector */}
      <div className="relative">
        <select
          value={settings.provider_id}
          aria-label="Provider"
          onChange={e => runAction(() => selectModel(state.providers.find(provider => provider.id === e.target.value)?.models[0]))}
          className="appearance-none bg-surface border border-white/10 rounded-md py-1.5 pl-3 pr-8 text-xs font-medium focus:ring-1 focus:ring-primary outline-none cursor-pointer hover:bg-white/5 transition-colors"
        >
          {!currentProvider && <option value={settings.provider_id}>{settings.provider_id} (unavailable)</option>}
          {state.providers.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <ChevronDown size={12} className="absolute right-2 top-1/2 -translate-y-1/2 opacity-50 pointer-events-none" />
      </div>

      <span className="text-white/20">/</span>

      {/* Model Selector */}
      <div className="relative">
        <select
          value={settings.model_id}
          aria-label="Model"
          onChange={e => runAction(() => selectModel(currentProvider?.models.find(model => model.id === e.target.value)))}
          className="appearance-none bg-surface border border-white/10 rounded-md py-1.5 pl-3 pr-8 text-xs font-medium focus:ring-1 focus:ring-primary outline-none cursor-pointer hover:bg-white/5 transition-colors max-w-[200px] truncate"
        >
          {!currentProvider?.models.some(model => model.id === settings.model_id) && <option value={settings.model_id}>{settings.model_id} (unavailable)</option>}
          {currentProvider?.models.map(m => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
        <ChevronDown size={12} className="absolute right-2 top-1/2 -translate-y-1/2 opacity-50 pointer-events-none" />
      </div>

      {/* Stats Pill */}
      <div className="whitespace-nowrap px-2 py-1.5 rounded-full bg-surface/50 border border-white/5 text-[10px] font-mono text-text-muted">
         RPM: {settings.rate_limit_rpm}
      </div>
    </div>
  );
}
