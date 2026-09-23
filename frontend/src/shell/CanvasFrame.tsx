import {
	ArrowLeft,
	Columns2,
	Maximize2,
	Menu as MenuIcon,
	MessageSquare,
	Send,
	X,
} from "lucide-react";
import type { ReactNode } from "react";

export type LayoutMode = "conversation" | "split" | "canvas";

export function CanvasFrame({
	layout,
	onLayout,
	onClose,
	onShowConversation,
	onOpenNav,
	canPublish,
	onPublish,
	publishActive,
	children,
}: {
	layout: LayoutMode;
	onLayout: (layout: LayoutMode) => void;
	onClose: () => void;
	onShowConversation: () => void;
	onOpenNav: () => void;
	canPublish: boolean;
	onPublish: () => void;
	publishActive: boolean;
	children: ReactNode;
}) {
	return (
		<section className="canvas" aria-label="Canvas">
			<div className="canvas-bar">
				<button
					type="button"
					className="icon-btn nav-toggle canvas-nav-toggle"
					aria-label="Open navigation"
					onClick={onOpenNav}
				>
					<MenuIcon aria-hidden="true" />
				</button>
				<button
					type="button"
					className="btn btn-quiet btn-small narrow-only"
					onClick={onShowConversation}
				>
					<ArrowLeft aria-hidden="true" />
					Conversation
				</button>
				<div className="canvas-bar-spacer" />
				{canPublish && !publishActive ? (
					<button type="button" className="btn btn-small" onClick={onPublish}>
						<Send aria-hidden="true" />
						Publish release
					</button>
				) : null}
				<fieldset className="segmented wide-only" aria-label="Layout">
					<button
						type="button"
						className="icon-btn"
						aria-label="Conversation only"
						title="Conversation only"
						aria-pressed={layout === "conversation"}
						onClick={() => onLayout("conversation")}
					>
						<MessageSquare aria-hidden="true" />
					</button>
					<button
						type="button"
						className="icon-btn"
						aria-label="Conversation and canvas"
						title="Conversation and canvas"
						aria-pressed={layout === "split"}
						onClick={() => onLayout("split")}
					>
						<Columns2 aria-hidden="true" />
					</button>
					<button
						type="button"
						className="icon-btn"
						aria-label="Canvas only"
						title="Canvas only"
						aria-pressed={layout === "canvas"}
						onClick={() => onLayout("canvas")}
					>
						<Maximize2 aria-hidden="true" />
					</button>
				</fieldset>
				<button
					type="button"
					className="icon-btn"
					aria-label="Close canvas"
					title="Close canvas"
					onClick={onClose}
				>
					<X aria-hidden="true" />
				</button>
			</div>
			<div className="canvas-scroll">{children}</div>
		</section>
	);
}
