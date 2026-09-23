import { ShieldQuestion } from "lucide-react";

import type { AgentInterrupt } from "../agentStream";
import type { CoursePlan } from "../models";

type CoursePlanProposal = {
	title: string;
	audience: string;
	goals: string[];
	outcomes: string[];
	lectures: { id?: string; title: string; group?: string | null }[];
};

type PresentationProposal = {
	lecture_id: string;
	replace_all_slides?: boolean;
	slides: {
		layout: string;
		title?: string | null;
		purpose?: string | null;
		archived?: boolean;
	}[];
};

type ReconciliationProposal = {
	summary: string;
	entries: { path: string; content: string | null }[];
};

type Parsed =
	| { tool: "course_plan"; preview: CoursePlanProposal | null }
	| { tool: "presentation"; preview: PresentationProposal | null }
	| { tool: "presentation_delete"; lectureId: string | null }
	| { tool: "reconciliation"; preview: ReconciliationProposal | null }
	| { tool: "unknown" };

function parseCall<T>(message: string, name: string): T | null {
	const match = new RegExp(`${name}\\((\\{.*\\})\\)\\?$`, "s").exec(message);
	if (!match) return null;
	try {
		return JSON.parse(match[1]) as T;
	} catch {
		return null;
	}
}

function isReconciliation(value: unknown): value is ReconciliationProposal {
	if (typeof value !== "object" || value === null) return false;
	const candidate = value as Record<string, unknown>;
	return (
		typeof candidate.summary === "string" &&
		Array.isArray(candidate.entries) &&
		candidate.entries.length > 0 &&
		candidate.entries.every(
			(entry) =>
				typeof entry === "object" &&
				entry !== null &&
				typeof (entry as Record<string, unknown>).path === "string" &&
				((entry as Record<string, unknown>).content === null ||
					typeof (entry as Record<string, unknown>).content === "string"),
		)
	);
}

export function parseApproval(interrupt: AgentInterrupt): Parsed {
	const message = interrupt.message ?? "";
	if (message.includes("apply_reconciliation_patch(")) {
		const preview = parseCall<unknown>(message, "apply_reconciliation_patch");
		return {
			tool: "reconciliation",
			preview: isReconciliation(preview) ? preview : null,
		};
	}
	if (message.includes("delete_presentation(")) {
		const payload = parseCall<{ lecture_id?: string }>(
			message,
			"delete_presentation",
		);
		return {
			tool: "presentation_delete",
			lectureId: payload?.lecture_id ?? null,
		};
	}
	if (message.includes("replace_presentation(")) {
		const payload = parseCall<{ command?: PresentationProposal }>(
			message,
			"replace_presentation",
		);
		return { tool: "presentation", preview: payload?.command ?? null };
	}
	if (message.includes("replace_course_plan(")) {
		const payload = parseCall<{ command?: CoursePlanProposal }>(
			message,
			"replace_course_plan",
		);
		return { tool: "course_plan", preview: payload?.command ?? null };
	}
	return { tool: "unknown" };
}

export function ApprovalCard({
	approval,
	course,
	disabled,
	onResolve,
}: {
	approval: AgentInterrupt;
	course: CoursePlan | null;
	disabled: boolean;
	onResolve: (approved: boolean) => void;
}) {
	const parsed = parseApproval(approval);
	const lectureTitle = (lectureId: string | null | undefined) =>
		course?.lectures.find((lecture) => lecture.id === lectureId)?.title ??
		"this Lecture";

	let heading = "Approve this change?";
	let body: React.ReactNode = null;
	let scope: string | null = null;
	let reject = "Don't apply";
	let accept = "Apply";
	let danger = false;

	if (parsed.tool === "course_plan") {
		heading = course ? "Replace the Course Plan?" : "Save this Course Plan?";
		accept = "Save Course Plan";
		reject = course ? "Keep current plan" : "Not now";
		scope =
			"Saves the title, audience, goals, and Lecture list. No Slides are created yet.";
		body = parsed.preview ? (
			<div className="proposal">
				<p className="proposal-title">{parsed.preview.title}</p>
				<p className="meta">For {parsed.preview.audience}</p>
				<ol className="proposal-list">
					{parsed.preview.lectures.map((lecture, index) => (
						<li key={`${lecture.id ?? "new"}-${lecture.title}`}>
							<span className="proposal-index">{index + 1}</span>
							{lecture.title}
						</li>
					))}
				</ol>
			</div>
		) : (
			<p>The proposed plan could not be previewed.</p>
		);
	} else if (parsed.tool === "presentation") {
		const preview = parsed.preview;
		heading = preview
			? `${preview.replace_all_slides ? "Replace" : "Update"} Slides for ${lectureTitle(preview.lecture_id)}?`
			: "Apply this Presentation change?";
		body = preview ? (
			<ol className="proposal-list">
				{preview.slides.map((slide, index) => (
					<li key={`${slide.layout}-${slide.title ?? index}`}>
						<span className="proposal-index">{index + 1}</span>
						<span>
							{slide.title ?? "Untitled Slide"}
							{slide.archived ? (
								<span className="meta"> · archived</span>
							) : null}
						</span>
					</li>
				))}
			</ol>
		) : (
			<p>The proposed Slides could not be previewed.</p>
		);
	} else if (parsed.tool === "presentation_delete") {
		heading = `Delete the Presentation for ${lectureTitle(parsed.lectureId)}?`;
		body = (
			<p>
				The Lecture stays in the Course Plan. You can restore the Slides from
				History.
			</p>
		);
		accept = "Delete Presentation";
		reject = "Keep it";
		danger = true;
	} else if (parsed.tool === "reconciliation") {
		heading = "Apply this fix for outside changes?";
		accept = "Apply fix";
		reject = "Not now";
		scope =
			"Applies exactly these file changes, checks the Course, and saves a Course Revision.";
		body = parsed.preview ? (
			<div className="proposal">
				<p className="proposal-title">{parsed.preview.summary}</p>
				<ul className="proposal-files">
					{parsed.preview.entries.map((entry) => (
						<li key={entry.path}>
							<details>
								<summary>
									{entry.content === null ? "Remove" : "Replace"}{" "}
									<code>{entry.path}</code>
								</summary>
								{entry.content === null ? (
									<p className="meta">This file will be removed.</p>
								) : (
									<pre>{entry.content}</pre>
								)}
							</details>
						</li>
					))}
				</ul>
			</div>
		) : (
			<p>The proposed fix could not be previewed, so it cannot be applied.</p>
		);
	} else {
		body = approval.message ? <p>{approval.message}</p> : null;
	}

	return (
		<section className="approval" aria-labelledby="approval-heading">
			<div className="approval-head">
				<ShieldQuestion aria-hidden="true" />
				<h3 id="approval-heading">{heading}</h3>
			</div>
			{body}
			{scope ? <p className="meta">{scope}</p> : null}
			<div className="approval-actions">
				<button
					type="button"
					className="btn"
					disabled={disabled}
					onClick={() => onResolve(false)}
				>
					{reject}
				</button>
				<button
					type="button"
					className={danger ? "btn btn-danger" : "btn btn-primary"}
					disabled={
						disabled ||
						(parsed.tool === "reconciliation" && parsed.preview === null)
					}
					onClick={() => onResolve(true)}
				>
					{accept}
				</button>
			</div>
		</section>
	);
}
