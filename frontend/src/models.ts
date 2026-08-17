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

export type ResourceKind = "local-file" | "upload" | "remote";

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
	indexed: boolean;
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

export type Candidate = {
	provider: string;
	provider_id: string;
	title: string | null;
	authors: string[] | null;
	summary: string | null;
	url: string;
	media_type: string | null;
	size_bytes: number | null;
	published_at: string | null;
};

export type DiscoveryResult = {
	provider: string;
	candidates: Candidate[];
	error: string | null;
};

export type SlideLayout =
	| "title"
	| "section"
	| "bullets"
	| "two_column"
	| "big_statement"
	| "closing"
	| "code"
	| "image"
	| "quote";

export type SlideCitation = {
	source_id: string;
	label: string;
	url?: string;
	line_start?: number;
	line_end?: number;
};

export type Slide = {
	id: string;
	layout: SlideLayout;
	title?: string;
	subtitle?: string;
	bullets?: string[];
	left_content?: string;
	right_content?: string;
	statement?: string;
	text?: string;
	code?: string;
	language?: string;
	image_url?: string;
	caption?: string;
	quote?: string;
	attribution?: string;
	speaker_notes?: string;
	purpose?: string;
	citations: SlideCitation[];
	archived: boolean;
};

export type Presentation = {
	schema_version: 1;
	id: string;
	lecture_id: string;
	slides: Slide[];
};

export type PresentationSummary = {
	id: string;
	lecture_id: string;
	slide_count: number;
};

export type TemplateLayoutMapping = {
	semantic_layout: SlideLayout;
	template_layout_index: number;
	confidence: number;
	rationale: string;
	slot_mappings: Record<string, number>;
};

export type TemplateProfile = {
	schema_version: 1;
	id: string;
	name: string;
	version: number;
	template_filename: string;
	slide_width: number;
	slide_height: number;
	slide_count: number;
	layouts: TemplateLayoutMapping[];
};

export type TemplateProfileSummary = {
	id: string;
	name: string;
	version: number;
	slide_count: number;
	mapped_layouts: number;
};

export type TemplatePlaceholderInspection = {
	idx: number;
	type: number;
	name: string;
	left: number;
	top: number;
	width: number;
	height: number;
};

export type TemplateLayoutInspection = {
	index: number;
	name: string;
	placeholders: TemplatePlaceholderInspection[];
	master_index: number;
	master_name: string;
	master_placeholders: TemplatePlaceholderInspection[];
};

export type TemplateInspection = {
	slide_width: number;
	slide_height: number;
	slide_count: number;
	layouts: TemplateLayoutInspection[];
	masters: unknown[];
	theme: {
		name: string;
		colors: Record<string, string>;
		fonts: Record<string, string>;
	};
	example_slides: unknown[];
	semantic_slots: Record<SlideLayout, string[]>;
};

export type TemplateValidationFinding = {
	level: "blocking" | "warning";
	message: string;
};

export type CalibrationSlide = {
	semantic_layout: string;
	template_layout_index: number;
	image_url: string;
};

export type PreviewSlot = {
	left: number;
	top: number;
	width: number;
	height: number;
	font_family: string;
	font_size: number;
	bold: boolean;
	alignment: string | null;
};

export type SlidePreviewDescriptor = {
	slide_id: string;
	layout: SlideLayout;
	slots: Record<string, PreviewSlot>;
	background_url: string | null;
	thumbnail_url: string | null;
};

export type PresentationPreview = {
	profile_id: string;
	profile_version: number;
	slide_width: number;
	slide_height: number;
	renderer: { available: boolean; name: string; detail: string };
	render_key: string;
	slides: SlidePreviewDescriptor[];
};
