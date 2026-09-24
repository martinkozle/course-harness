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
	media_type?: string | null;
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
	doi?: string | null;
	arxiv_id?: string | null;
	venue?: string | null;
	citation_count?: number | null;
	open_access_url?: string | null;
};

/** A Candidate a research tool returned in the Conversation. */
export type ResearchCandidate = {
	url: string;
	title?: string;
	authors?: string[];
	published?: string;
	venue?: string;
	doi?: string;
	arxiv_id?: string;
	citations?: number;
	open_access_url?: string;
	provider?: string;
	summary?: string;
};

export type ResearchCard = {
	title: string;
	candidates: ResearchCandidate[];
	errors?: Record<string, string>;
};

export function candidateFromResearch(item: ResearchCandidate): Candidate {
	return {
		provider: item.provider ?? "",
		provider_id: item.url,
		title: item.title ?? null,
		authors: item.authors ?? null,
		summary: item.summary ?? null,
		url: item.url,
		media_type: null,
		size_bytes: null,
		published_at: item.published ?? null,
		doi: item.doi ?? null,
		arxiv_id: item.arxiv_id ?? null,
		venue: item.venue ?? null,
		citation_count: item.citations ?? null,
		open_access_url: item.open_access_url ?? null,
	};
}

/** The Library Resource captured for a Candidate, from either of its addresses. */
export function candidateResource(
	resources: ResourceState[],
	candidate: Candidate,
): ResourceState | undefined {
	return resources.find(
		(resource) =>
			resource.location === candidate.url ||
			(candidate.open_access_url != null &&
				resource.location === candidate.open_access_url),
	);
}

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
	image_source_id?: string | null;
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
	render_key: string;
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

export type CurrentStateFile = {
	path: string;
	status: "added" | "modified" | "deleted";
	line_count: number | null;
	diff_lines: string[];
	diff_truncated: boolean;
};

export type CurrentState = {
	clean: boolean;
	changes: CurrentStateFile[];
	validation: { valid: boolean; findings: string[] };
	drift: "unknown" | "clean" | "drift";
	drift_id: string | null;
	drift_changes: CurrentStateFile[];
};

export type CourseRevision = {
	id: string;
	summary: string;
	created_at: string;
};

export type ReleaseSelection = {
	lecture_ids: string[];
	artifact_ids: string[];
};

export type ReleaseFindingTarget = {
	scope:
		| "release"
		| "lecture"
		| "artifact"
		| "slide"
		| "content_block"
		| "citation";
	lecture_id: string | null;
	artifact_id: string | null;
	slide_id: string | null;
	content_block: string | null;
	citation_index: number | null;
	source_id: string | null;
	line_start: number | null;
	line_end: number | null;
};

export type EvidenceTarget = {
	source_id: string | null;
	line_start: number | null;
	line_end: number | null;
};

export type ReleaseValidationFinding = {
	id: string;
	code: string;
	severity: "error" | "warning";
	message: string;
	target: ReleaseFindingTarget;
	waived: boolean;
};

export type ReleaseWaiver = {
	finding_id: string;
	justification: string;
};

export type ReleaseValidation = {
	selection: ReleaseSelection;
	findings: ReleaseValidationFinding[];
	waivers: ReleaseWaiver[];
	structurally_valid: boolean;
	can_publish: boolean;
};

export type ReleaseArtifact = {
	id: string;
	lecture_id: string;
	media_type: string;
	storage_path: string;
	size: number;
	sha256: string;
};

export type ReleaseSourcePin = {
	id: string;
	resource_id: string;
	source_version_id: string;
	label: string;
};

export type ReleaseTemplatePin = {
	id: string;
	version: number;
	profile_sha256: string;
	template_sha256: string | null;
	template_storage_path: "inputs/template.pptx" | null;
	definition: TemplateProfile;
};

export type CourseRelease = {
	schema_version: 1;
	slug: string;
	name: string;
	tag: string;
	course_id: string;
	revision_id: string;
	commit_oid: string;
	published_at: string;
	included_lecture_ids: string[];
	planned_unpublished_lecture_ids: string[];
	sources: ReleaseSourcePin[];
	template_profile: ReleaseTemplatePin;
	validation: ReleaseValidation;
	artifacts: ReleaseArtifact[];
};

export type Lecture = {
	id: string;
	title: string;
	group: string | null;
	presentation_id?: string;
};

export type CoursePlan = {
	schema_version: 1;
	id: string;
	title: string;
	audience: string;
	goals: string[];
	outcomes: string[];
	template_profile_id?: string | null;
	template_profile_version?: number | null;
	lectures: Lecture[];
};

export type Workspace = {
	name: string;
	path: string;
};

export type WorkspaceEntry = {
	path: string;
	kind: "file" | "directory";
};

/** A passage to open in the Source reader, optionally narrowed to lines. */
export type ReaderTarget = {
	sourceId: string | null;
	resourceId: string | null;
	label: string;
	lineStart: number | null;
	lineEnd: number | null;
};

export type ConnectorHeader = {
	name: string;
	value: string | null;
	secret: boolean;
	configured: boolean;
};

export type Connector = {
	id: string;
	name: string;
	url: string;
	url_secret: boolean;
	headers: ConnectorHeader[];
	enabled: boolean;
	hidden_tools: string[];
	fetch_tool: string | null;
	preset: "exa" | null;
	credential_storage:
		| "os-keyring"
		| "private-json-file"
		| "unavailable"
		| "unconfigured"
		| null;
};

export type ConnectorTool = { name: string; description: string | null };

export type ConnectorInput = {
	name: string;
	url: string;
	url_secret: boolean;
	headers: { name: string; value: string | null; secret: boolean }[];
	enabled: boolean;
	hidden_tools: string[];
	fetch_tool: string | null;
};
