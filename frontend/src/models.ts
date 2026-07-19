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

export type ResourceKind = "local-file" | "upload";

export type ProcessingStatus =
	| "unprocessed"
	| "processing"
	| "ready"
	| "failed"
	| "retrying";

export type Snapshot = {
	resource_id: string;
	content_hash: string;
	byte_count: number;
	captured_at: string;
};

export type ResourceState = {
	resource_id: string;
	kind: ResourceKind | null;
	location: string | null;
	status: ProcessingStatus;
	error: string | null;
	snapshot: Snapshot | null;
};

export type Source = {
	id: string;
	resource_id: string;
	source_version_id: string;
	label: string;
	admitted_at: string;
};

export type SearchResult = {
	source_id: string;
	resource_id: string;
	label: string;
	snippet: string;
	coordinates: { line_start: number | null; line_end: number | null };
	rank: number;
};

export type GroupedSearchResult = {
	source_id: string;
	resource_id: string;
	label: string;
	max_rank: number;
	chunks: SearchResult[];
};
