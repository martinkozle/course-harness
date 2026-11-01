import {
	ArrowLeft,
	Maximize2,
	Menu as MenuIcon,
	Minimize2,
	Send,
	X,
} from "lucide-react";
import type { ReactNode } from "react";

/** "conversation" is only the effective layout when no canvas is open. */
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
				<button
					type="button"
					className="icon-btn wide-only"
					aria-label={
						layout === "canvas" ? "Show conversation" : "Expand canvas"
					}
					title={layout === "canvas" ? "Show conversation" : "Expand canvas"}
					aria-pressed={layout === "canvas"}
					onClick={() => onLayout(layout === "canvas" ? "split" : "canvas")}
				>
					{layout === "canvas" ? (
						<Minimize2 aria-hidden="true" />
					) : (
						<Maximize2 aria-hidden="true" />
					)}
				</button>
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
