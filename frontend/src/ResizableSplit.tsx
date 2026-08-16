import {
	type CSSProperties,
	type PointerEvent,
	type ReactNode,
	useEffect,
	useRef,
	useState,
} from "react";

type ResizableSplitProps = {
	primary: ReactNode;
	secondary: ReactNode;
	storageKey: string;
};

const DEFAULT_SECONDARY_PERCENT = 32;

function storedPercent(storageKey: string): number {
	const stored = Number.parseFloat(
		window.localStorage.getItem(storageKey) ?? "",
	);
	return Number.isFinite(stored)
		? Math.min(Math.max(stored, 25), 50)
		: DEFAULT_SECONDARY_PERCENT;
}

function splitBounds(width: number): { min: number; max: number } {
	if (width < 912) return { min: 25, max: 50 };
	return {
		min: Math.max(25, (340 / width) * 100),
		max: Math.min(50, ((width - 560) / width) * 100),
	};
}

export function ResizableSplit({
	primary,
	secondary,
	storageKey,
}: ResizableSplitProps) {
	const containerRef = useRef<HTMLDivElement>(null);
	const [secondaryPercent, setSecondaryPercent] = useState(() =>
		storedPercent(storageKey),
	);
	const [activePane, setActivePane] = useState<"primary" | "secondary">(
		"primary",
	);
	const [limits, setLimits] = useState({ min: 25, max: 50 });

	useEffect(() => {
		const container = containerRef.current;
		if (!container) return;
		const clampToContainer = () => {
			const nextLimits = splitBounds(container.getBoundingClientRect().width);
			setLimits(nextLimits);
			setSecondaryPercent((current) => {
				const next = Math.min(
					Math.max(current, nextLimits.min),
					nextLimits.max,
				);
				window.localStorage.setItem(storageKey, next.toFixed(2));
				return next;
			});
		};
		clampToContainer();
		const observer = new ResizeObserver(clampToContainer);
		observer.observe(container);
		return () => observer.disconnect();
	}, [storageKey]);

	function resize(nextPercent: number) {
		const width = containerRef.current?.getBoundingClientRect().width ?? 1200;
		const { min, max } = splitBounds(width);
		setLimits({ min, max });
		const next = Math.min(Math.max(nextPercent, min), max);
		setSecondaryPercent(next);
		window.localStorage.setItem(storageKey, next.toFixed(2));
	}

	function resizeFromPointer(event: PointerEvent<HTMLHRElement>) {
		const rect = containerRef.current?.getBoundingClientRect();
		if (!rect) return;
		resize(((rect.right - event.clientX) / rect.width) * 100);
	}

	return (
		<div
			className="authoring-split"
			ref={containerRef}
			data-active-pane={activePane}
			style={{ "--agent-pane-width": `${secondaryPercent}%` } as CSSProperties}
		>
			<fieldset className="authoring-pane-tabs">
				<legend>Authoring panels</legend>
				<button
					type="button"
					aria-pressed={activePane === "primary"}
					onClick={() => setActivePane("primary")}
				>
					Presentation
				</button>
				<button
					type="button"
					aria-pressed={activePane === "secondary"}
					onClick={() => setActivePane("secondary")}
				>
					Course Agent
				</button>
			</fieldset>
			<div className="authoring-primary-pane">{primary}</div>
			<hr
				className="authoring-resizer"
				aria-label="Resize Presentation and Course Agent"
				aria-orientation="vertical"
				aria-valuemin={Math.round(limits.min)}
				aria-valuemax={Math.round(limits.max)}
				aria-valuenow={Math.round(secondaryPercent)}
				tabIndex={0}
				onPointerDown={(event) => {
					event.currentTarget.setPointerCapture(event.pointerId);
					resizeFromPointer(event);
				}}
				onPointerMove={(event) => {
					if (event.currentTarget.hasPointerCapture(event.pointerId)) {
						resizeFromPointer(event);
					}
				}}
				onDoubleClick={() => resize(DEFAULT_SECONDARY_PERCENT)}
				onKeyDown={(event) => {
					const step = event.shiftKey ? 10 : 2;
					if (event.key === "ArrowLeft") {
						event.preventDefault();
						resize(secondaryPercent + step);
					} else if (event.key === "ArrowRight") {
						event.preventDefault();
						resize(secondaryPercent - step);
					} else if (event.key === "Home") {
						event.preventDefault();
						resize(limits.min);
					} else if (event.key === "End") {
						event.preventDefault();
						resize(limits.max);
					}
				}}
			/>
			<div className="authoring-secondary-pane">{secondary}</div>
		</div>
	);
}
