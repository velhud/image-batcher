import { GlobalSettingsDTO, ProviderInfo, RowSettingsDTO } from '@/api/types';

export function modelForSettings(providers: ProviderInfo[], settings: Pick<GlobalSettingsDTO, 'provider_id' | 'model_id'>) {
  return providers.find(p => p.id === settings.provider_id)?.models.find(m => m.id === settings.model_id);
}

export function effectiveRowSettings(global: GlobalSettingsDTO, row: RowSettingsDTO): GlobalSettingsDTO {
  const values = Object.fromEntries(Object.entries(row).filter(([, value]) => value !== null && value !== undefined));
  const result = { ...global, ...values };
  if (row.size_preset && row.size_preset !== 'Custom' && !row.custom_size) result.custom_size = null;
  return result;
}

export const resetRowSettings: RowSettingsDTO = {
  provider_id: null, model_id: null, size_preset: null, custom_size: null,
  num_images: null, style_preset: null, negative_prompt: null, seed: null,
  random_seed: null, quality: null, safety: null, keep_images: null,
  generate_behavior: null, regen_use_same_seed: null, thinking_budget: null,
  thinking_level: null, output_format: null, background: null,
};

export const qualityLabel = (value: number): string => value <= 3 ? 'low' : value >= 8 ? 'high' : 'medium';
export const qualityValue = (value: string): number => value === 'low' ? 2 : value === 'high' ? 8 : 5;
