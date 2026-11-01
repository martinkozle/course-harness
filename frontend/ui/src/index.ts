// Components and design tokens shared by the Harness and Classroom interfaces.
// Styles: import "@course-harness/ui/styles.css" before an app's own styles.
export {
	CitationChip,
	CitationList,
	type CitationOpening,
} from "./Citations";
export {
	ConfirmDialog,
	Dialog,
	downloadResponse,
	errorMessage,
	isAbort,
	Menu,
	type MenuItem,
	Notice,
	Tabs,
} from "./primitives";
export { SlidePreview } from "./SlidePreview";
export {
	lineRangeLabel,
	type PreviewSlot,
	type Slide,
	type SlideCitation,
	type SlideLayout,
	slideSummary,
} from "./slides";
