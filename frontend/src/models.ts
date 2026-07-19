export type ProviderKind = "openrouter" | "openai-compatible" | "anthropic";

export type ProviderCapabilities = {
  tool_calling: boolean;
  structured_output: boolean;
  streaming: boolean;
  context_window: number;
  vision: boolean;
};

export type ProviderAccount = {
  id: string;
  name: string;
  kind: ProviderKind;
  base_url: string;
};

export type ModelPreset = {
  id: string;
  name: string;
  provider_account_id: string;
  model: string;
  capabilities: ProviderCapabilities;
  diagnostics: string[];
};

export type ModelCatalog = {
  provider_accounts: ProviderAccount[];
  model_presets: ModelPreset[];
  selected_model_id: string | null;
};
