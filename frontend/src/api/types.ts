export type RowStatus = "Idle" | "Queued" | "Generating" | "Completed" | "Error" | "Filtered" | "Cancelled" | "Interrupted";

export interface ProviderInfo {
  id: string;
  name: string;
  rate_limit_note?: string;
  models: ModelInfo[];
}

export interface ModelInfo {
  id: string;
  name: string;
  provider_id: string;
  capabilities?: ProviderCapabilitiesDTO;
}

export interface ProviderCapabilitiesDTO {
  max_images: number;
  supports_style: boolean;
  supports_negative_prompt: boolean;
  supports_seed: boolean;
  supports_quality: boolean;
  supports_safety: boolean;
  supports_attachments: boolean;
  max_attachments: number;
  supports_custom_size: boolean;
  output_formats: string[];
  supports_background: boolean;
  quality_options: string[];
  size_presets: Record<string, SizePresetDTO>;
}

export interface SizePresetDTO {
  width: number;
  height: number;
}

export interface InputAttachmentDTO {
  id: string;
  file_path: string;
  mime_type: string;
}

export interface GlobalSettingsDTO {
  provider_id: string;
  model_id: string;
  size_preset: string;
  custom_size?: SizePresetDTO | null;
  num_images: number;
  style_preset: string;
  negative_prompt: string;
  seed?: number | null;
  random_seed: boolean;
  quality: number;
  safety: number;
  export_folder?: string | null;
  naming_pattern: string;
  concurrency_limit: number;
  generate_behavior: string;
  regen_use_same_seed: boolean;
  confirm_generate_threshold: number;
  rate_limit_rpm: number;
  thinking_budget: number;
  thinking_level: string;
  prompt_wrapper: string;
  output_format: string;
  background: string;
}

export interface ImageResultDTO {
  id: string;
  row_id: string;
  file_path: string;
  metadata?: Record<string, any>;
}

export interface RowSettingsDTO {
  provider_id?: string | null;
  model_id?: string | null;
  size_preset?: string | null;
  custom_size?: SizePresetDTO | null;
  num_images?: number | null;
  style_preset?: string | null;
  negative_prompt?: string | null;
  seed?: number | null;
  random_seed?: boolean | null;
  quality?: number | null;
  safety?: number | null;
  keep_images?: boolean | null;
  generate_behavior?: string | null;
  regen_use_same_seed?: boolean | null;
  thinking_budget?: number | null;
  thinking_level?: string | null;
  output_format?: string | null;
  background?: string | null;
}

export interface RowDTO {
  id: string;
  prompt: string;
  prompt_id: string;
  category_id: string;
  source_metadata: Record<string, any>;
  status: RowStatus;
  error_message: string;
  selected: boolean;
  settings: RowSettingsDTO;
  attachments: InputAttachmentDTO[];
  images: ImageResultDTO[];
  tags: string[];
}

export interface PromptTemplateDTO {
  name: string;
  template: string;
  variables: Record<string, string[]>;
}

export interface SessionDTO {
  id: string;
  global_settings: GlobalSettingsDTO;
  rows: RowDTO[];
  templates: PromptTemplateDTO[];
}

export interface StatsDTO {
  total: number;
  completed: number;
  errors: number;
  average_duration: number;
  per_provider?: Record<string, number>;
}

export interface BackendState {
  session: SessionDTO;
  providers: ProviderInfo[];
  stats: StatsDTO;
  restore_warning?: string;
}

export interface ParsedBatchRowDTO {
  prompt: string;
  prompt_id: string;
  category_id: string;
  source_metadata: Record<string, string>;
}

export interface BatchParseResponseDTO {
  prompts: string[];
  rows: ParsedBatchRowDTO[];
  errors: string[];
}

export interface ProviderStatusDTO {
  azure_openai: {
    configured: boolean;
    missing: string[];
    endpoint: string;
    deployment: string;
    api_version: string;
  };
  azure_logo_wrapper: string;
}

export interface BatchVerificationDTO {
  total_prompts: number;
  successful_prompts: number;
  image_count: number;
  requested_image_count: number;
  filtered_count: number;
  failed_count: number;
  missing_count: number;
  skipped_count: number;
  matches_prompt_count: boolean;
  successful_prompt_ids: string[];
  filtered_prompt_ids: string[];
  failed_prompt_ids: string[];
  missing_prompt_ids: string[];
  skipped_prompt_ids: string[];
}

export interface PreflightRowDTO {
  row_id: string;
  prompt_id: string;
  provider: string;
  model: string;
  requested_images: number;
  errors: string[];
}

export interface PreflightDTO {
  valid: boolean;
  rows: PreflightRowDTO[];
  total_requests: number;
  total_images: number;
}

export interface ExportBundleDTO {
  path: string;
  exported: number;
  missing: Array<{ row_id: string; kind: string; name: string }>;
  manifest_path: string;
  contact_sheet: string | null;
}

// RPC Response Wrappers
export interface RPCResponse<T> {
  ok: boolean;
  data?: T;
  error?: string;
}
