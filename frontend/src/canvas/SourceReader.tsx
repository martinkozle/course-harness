import { ArrowLeft, MessageSquareText } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { responseError } from "../api";
import type { ReaderTarget } from "../models";
import { errorMessage, isAbort, Notice, Tabs } from "../ui";
import type { AgentContext } from "../useCourseAgent";

type Content =
	| { kind: "loading" }
	| { kind: "text"; text: string }
	| { kind: "pdf"; url: string }
	| { kind: "image"; url: string }
	| { kind: "error"; message: string };

export function lineRangeLabel(
	lineStart: number | null,
	lineEnd: number | null,
): string | null {
	if (lineStart === null) return null;
	if (lineEnd === null || lineEnd === lineStart) return `Line ${lineStart + 1}`;
	return `Lines ${lineStart + 1}–${lineEnd + 1}`;
}

export function SourceReader({
	target,
	returnLabel,
	onReturn,
	onAskAgent,
}: {
	target: ReaderTarget;
	returnLabel: string | null;
	onReturn: () => void;
	onAskAgent: (request: string, context?: AgentContext | null) => void;
}) {
	const range = lineRangeLabel(target.lineStart, target.lineEnd);
	const hasText = Boolean(target.sourceId);
	const hasOriginal = Boolean(target.resourceId);
	// A cited passage opens on the extracted text; browsing a file opens on the original.
	const [view, setView] = useState<"text" | "original">(
		hasText && (range || !hasOriginal) ? "text" : "original",
	);
	const [text, setText] = useState<Content>({ kind: "loading" });
	const [original, setOriginal] = useState<Content>({ kind: "loading" });
	const content = view === "text" ? text : original;
	const highlightRef = useRef<HTMLSpanElement | null>(null);

	useEffect(() => {
		if (!target.sourceId) return;
		const controller = new AbortController();
		setText({ kind: "loading" });
		void (async () => {
			try {
				const response = await fetch(
					`/api/sources/${encodeURIComponent(target.sourceId ?? "")}/content?max_chars=400000`,
					{ signal: controller.signal },
				);
				if (!response.ok) throw new Error(await responseError(response));
				setText({ kind: "text", text: await response.text() });
			} catch (caught) {
				if (!isAbort(caught))
					setText({
						kind: "error",
						message: errorMessage(
							caught,
							"The extracted text could not be opened.",
						),
					});
			}
		})();
		return () => controller.abort();
	}, [target.sourceId]);

	useEffect(() => {
		if (view !== "original") return;
		const controller = new AbortController();
		let objectUrl: string | null = null;
		setOriginal({ kind: "loading" });
		void (async () => {
			try {
				if (target.resourceId) {
					const response = await fetch(
						`/api/resources/${encodeURIComponent(target.resourceId)}/preview`,
						{ signal: controller.signal },
					);
					if (!response.ok) throw new Error(await responseError(response));
					const type = response.headers.get("content-type") ?? "";
					if (type.startsWith("application/pdf") || type.startsWith("image/")) {
						objectUrl = URL.createObjectURL(await response.blob());
						if (!controller.signal.aborted)
							setOriginal({
								kind: type.startsWith("image/") ? "image" : "pdf",
								url: objectUrl,
							});
					} else {
						setOriginal({ kind: "text", text: await response.text() });
					}
					return;
				}
				setOriginal({
					kind: "error",
					message: "The original file is no longer in your Library.",
				});
			} catch (caught) {
				if (!isAbort(caught))
					setOriginal({
						kind: "error",
						message: errorMessage(caught, "This file could not be opened."),
					});
			}
		})();
		return () => {
			controller.abort();
			if (objectUrl) URL.revokeObjectURL(objectUrl);
		};
	}, [target.resourceId, view]);

	useEffect(() => {
		if (view === "text" && text.kind === "text")
			highlightRef.current?.scrollIntoView({ block: "center" });
	}, [text, view]);

	const lines = content.kind === "text" ? content.text.split("\n") : [];
	const showHighlight = view === "text";
	const start = target.lineStart;
	const end = target.lineEnd ?? target.lineStart;
	const highlighted = (index: number) =>
		start !== null && end !== null && index >= start && index <= end;

	return (
		<div className="canvas-page reader">
			{returnLabel ? (
				<button type="button" className="back-link" onClick={onReturn}>
					<ArrowLeft aria-hidden="true" />
					{returnLabel}
				</button>
			) : null}
			<header className="canvas-head">
				<div className="canvas-head-copy">
					<h1 className="display-title reader-title">{target.label}</h1>
					{range ? <p className="meta">{range}</p> : null}
				</div>
				{target.sourceId ? (
					<div className="canvas-head-actions">
						<button
							type="button"
							className="btn"
							onClick={() =>
								onAskAgent(
									range
										? "Explain how this passage should be used in the Course."
										: "Summarize what this Source can contribute to the Course.",
									{
										key: `reader-${target.sourceId}-${target.lineStart ?? "all"}-${target.lineEnd ?? "all"}`,
										label: range ? `${target.label} · ${range}` : target.label,
										instruction: range
											? `I'm looking at ${target.label}, ${range.toLowerCase()}`
											: `I'm looking at the Source ${target.label}`,
									},
								)
							}
						>
							<MessageSquareText aria-hidden="true" />
							{range ? "Ask about this passage" : "Ask about this Source"}
						</button>
					</div>
				) : null}
			</header>

			{hasText && hasOriginal ? (
				<div className="reader-views">
					<Tabs
						label="Show"
						value={view}
						onChange={setView}
						tabs={[
							{ id: "text", label: "Extracted text" },
							{ id: "original", label: "Original file" },
						]}
					/>
				</div>
			) : null}

			{content.kind === "loading" ? (
				<p className="meta" role="status">
					Opening…
				</p>
			) : content.kind === "error" ? (
				<Notice tone="error">{content.message}</Notice>
			) : content.kind === "image" ? (
				<figure className="reader-image panel">
					<img src={content.url} alt={target.label} />
				</figure>
			) : content.kind === "pdf" ? (
				<iframe
					className="reader-pdf"
					src={content.url}
					title={`PDF preview of ${target.label}`}
				/>
			) : (
				<ol
					className="reader-lines panel"
					aria-label={`Text of ${target.label}`}
				>
					{lines.map((line, index) => {
						const isHit = showHighlight && highlighted(index);
						const isFirstHit = isHit && index === start;
						return (
							<li
								// biome-ignore lint/suspicious/noArrayIndexKey: lines are positional
								key={index}
								className={isHit ? "reader-line is-cited" : "reader-line"}
								value={index + 1}
							>
								<span className="reader-line-number" aria-hidden="true">
									{index + 1}
								</span>
								<span
									className="reader-line-text"
									ref={isFirstHit ? highlightRef : undefined}
								>
									{isHit && line ? (
										<mark className="evidence-mark">{line}</mark>
									) : (
										line || " "
									)}
								</span>
							</li>
						);
					})}
				</ol>
			)}
		</div>
	);
}
