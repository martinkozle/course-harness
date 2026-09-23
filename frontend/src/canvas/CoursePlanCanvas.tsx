import {
	ArrowDown,
	ArrowUp,
	MoreHorizontal,
	Pencil,
	Plus,
	Presentation,
	Sparkles,
	Trash2,
} from "lucide-react";
import { type FormEvent, useState } from "react";

import { responseError } from "../api";
import type {
	CoursePlan,
	Lecture,
	PresentationSummary,
	Source,
} from "../models";
import { ConfirmDialog, Dialog, Menu, Notice, errorMessage } from "../ui";
import type { AgentContext } from "../useCourseAgent";

function lines(value: string): string[] {
	return value
		.split("\n")
		.map((line) => line.trim())
		.filter(Boolean);
}

async function send(path: string, method: string, body?: object) {
	const response = await fetch(path, {
		method,
		...(body
			? {
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(body),
				}
			: {}),
	});
	if (!response.ok) throw new Error(await responseError(response));
	return (await response.json()) as CoursePlan;
}

const planContext: AgentContext = {
	key: "plan",
	label: "Course Plan",
	instruction: "I'm looking at the Course Plan",
};

export function CoursePlanCanvas({
	course,
	courseError,
	presentations,
	sources,
	busy,
	onCourseChange,
	onRetry,
	onOpenLecture,
	onAskAgent,
}: {
	course: CoursePlan | null;
	courseError: string | null;
	presentations: PresentationSummary[];
	sources: Source[];
	busy: boolean;
	onCourseChange: (course: CoursePlan) => void;
	onRetry: () => void;
	onOpenLecture: (lectureId: string) => void;
	onAskAgent: (request: string, context?: AgentContext | null) => void;
}) {
	if (courseError) {
		return (
			<div className="canvas-page">
				<div className="canvas-empty">
					<h2 className="display-title">The Course Plan can't be read</h2>
					<p>
						Fix <code>course.yaml</code> in the course folder, then try again.
					</p>
					<Notice tone="error">{courseError}</Notice>
					<button type="button" className="btn btn-primary" onClick={onRetry}>
						Try again
					</button>
				</div>
			</div>
		);
	}
	if (!course) {
		return (
			<ManualPlan
				sources={sources}
				onCreated={onCourseChange}
				onAskAgent={onAskAgent}
			/>
		);
	}
	return (
		<PlanView
			course={course}
			presentations={presentations}
			sources={sources}
			busy={busy}
			onCourseChange={onCourseChange}
			onOpenLecture={onOpenLecture}
			onAskAgent={onAskAgent}
		/>
	);
}

function PlanView({
	course,
	presentations,
	sources,
	busy,
	onCourseChange,
	onOpenLecture,
	onAskAgent,
}: {
	course: CoursePlan;
	presentations: PresentationSummary[];
	sources: Source[];
	busy: boolean;
	onCourseChange: (course: CoursePlan) => void;
	onOpenLecture: (lectureId: string) => void;
	onAskAgent: (request: string, context?: AgentContext | null) => void;
}) {
	const [editingDetails, setEditingDetails] = useState(false);
	const [renaming, setRenaming] = useState<string | null>(null);
	const [removing, setRemoving] = useState<Lecture | null>(null);
	const [adding, setAdding] = useState(false);
	const [newTitle, setNewTitle] = useState("");
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const slides = new Map(
		presentations.map((presentation) => [
			presentation.lecture_id,
			presentation.slide_count,
		]),
	);

	async function mutate(action: () => Promise<CoursePlan>, fallback: string) {
		setSaving(true);
		setError(null);
		try {
			onCourseChange(await action());
			return true;
		} catch (caught) {
			setError(errorMessage(caught, fallback));
			return false;
		} finally {
			setSaving(false);
		}
	}

	function move(index: number, direction: -1 | 1) {
		const ids = course.lectures.map((lecture) => lecture.id);
		const target = index + direction;
		[ids[index], ids[target]] = [ids[target], ids[index]];
		void mutate(
			() => send("/api/course/lectures/order", "PUT", { lecture_ids: ids }),
			"The Lecture order could not be saved.",
		);
	}

	async function addLecture(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const title = newTitle.trim();
		if (!title) return;
		const done = await mutate(
			() => send("/api/course/lectures", "POST", { title }),
			"The Lecture could not be added.",
		);
		if (done) {
			setNewTitle("");
			setAdding(false);
		}
	}

	const locked = busy || saving;

	return (
		<div className="canvas-page plan-page">
			<header className="canvas-head">
				<div className="canvas-head-copy">
					<h1 className="display-title">{course.title}</h1>
					<p className="meta plan-audience">For {course.audience}</p>
				</div>
				<div className="canvas-head-actions">
					<button
						type="button"
						className="btn btn-quiet"
						onClick={() =>
							onAskAgent(
								"Review this Course Plan against the Sources and suggest improvements.",
								planContext,
							)
						}
					>
						<Sparkles aria-hidden="true" />
						Ask for a review
					</button>
					<button
						type="button"
						className="btn"
						disabled={locked}
						onClick={() => setEditingDetails(true)}
					>
						<Pencil aria-hidden="true" />
						Edit details
					</button>
				</div>
			</header>

			{error ? <Notice tone="error">{error}</Notice> : null}

			<section
				className="canvas-section"
				aria-labelledby="plan-lectures-heading"
			>
				<div className="canvas-section-head">
					<h2 id="plan-lectures-heading">Lectures</h2>
					<span className="meta">
						{course.lectures.length} in teaching order
						{sources.length > 0
							? ` · grounded in ${sources.length} Source${sources.length === 1 ? "" : "s"}`
							: ""}
					</span>
				</div>
				<ol className="row-list plan-lectures" aria-label="Lecture sequence">
					{course.lectures.map((lecture, index) => {
						const count = slides.get(lecture.id);
						return (
							<li key={lecture.id} className="row plan-lecture">
								<span className="plan-number" aria-hidden="true">
									{index + 1}
								</span>
								{renaming === lecture.id ? (
									<RenameLecture
										lecture={lecture}
										index={index}
										disabled={locked}
										onCancel={() => setRenaming(null)}
										onSave={async (title) => {
											const done = await mutate(
												() =>
													send(
														`/api/course/lectures/${encodeURIComponent(lecture.id)}`,
														"PATCH",
														{ title },
													),
												"The Lecture could not be renamed.",
											);
											if (done) setRenaming(null);
										}}
									/>
								) : (
									<>
										<div className="row-main">
											<button
												type="button"
												className="row-title plan-lecture-title"
												onClick={() => onOpenLecture(lecture.id)}
											>
												{lecture.title}
											</button>
											<span className="row-meta">
												{count ? (
													<>
														<Presentation
															aria-hidden="true"
															className="plan-slides-icon"
														/>
														{count} Slides
													</>
												) : (
													"No Slides yet"
												)}
											</span>
										</div>
										<div className="row-actions">
											{!count ? (
												<button
													type="button"
													className="btn btn-quiet btn-small"
													onClick={() =>
														onAskAgent(
															`Create Slides for “${lecture.title}” grounded in the course Sources.`,
															{
																key: `lecture-${lecture.id}`,
																label: `Lecture ${index + 1} · ${lecture.title}`,
																instruction: `I'm working on the Lecture "${lecture.title}"`,
															},
														)
													}
												>
													Draft Slides
												</button>
											) : null}
											<Menu
												label={`Actions for ${lecture.title}`}
												trigger={<MoreHorizontal aria-hidden="true" />}
												disabled={locked}
												items={[
													{
														label: "Rename",
														icon: <Pencil aria-hidden="true" />,
														onSelect: () => setRenaming(lecture.id),
													},
													{
														label: "Move earlier",
														icon: <ArrowUp aria-hidden="true" />,
														disabled: index === 0,
														onSelect: () => move(index, -1),
													},
													{
														label: "Move later",
														icon: <ArrowDown aria-hidden="true" />,
														disabled: index === course.lectures.length - 1,
														onSelect: () => move(index, 1),
													},
													"separator",
													{
														label: "Remove Lecture",
														icon: <Trash2 aria-hidden="true" />,
														danger: true,
														disabled:
															Boolean(count) || course.lectures.length === 1,
														title: count
															? "Delete its Presentation first"
															: course.lectures.length === 1
																? "A Course Plan needs at least one Lecture"
																: undefined,
														onSelect: () => setRemoving(lecture),
													},
												]}
											/>
										</div>
									</>
								)}
							</li>
						);
					})}
				</ol>
				{adding ? (
					<form
						className="plan-add"
						onSubmit={(event) => void addLecture(event)}
					>
						<label htmlFor="new-lecture-title" className="visually-hidden">
							New Lecture title
						</label>
						<input
							id="new-lecture-title"
							className="input"
							value={newTitle}
							maxLength={200}
							placeholder="Lecture title"
							// biome-ignore lint/a11y/noAutofocus: revealed by an explicit action
							autoFocus
							onChange={(event) => setNewTitle(event.target.value)}
							onKeyDown={(event) => {
								if (event.key === "Escape") setAdding(false);
							}}
						/>
						<button
							type="button"
							className="btn"
							onClick={() => setAdding(false)}
						>
							Cancel
						</button>
						<button
							type="submit"
							className="btn btn-primary"
							disabled={!newTitle.trim() || locked}
						>
							Add Lecture
						</button>
					</form>
				) : (
					<button
						type="button"
						className="btn btn-quiet plan-add-trigger"
						disabled={locked}
						onClick={() => setAdding(true)}
					>
						<Plus aria-hidden="true" />
						Add Lecture
					</button>
				)}
			</section>

			<section
				className="canvas-section plan-intent"
				aria-label="Course intent"
			>
				<div>
					<h2 className="section-label">Goals</h2>
					{course.goals.length > 0 ? (
						<ul>
							{course.goals.map((goal) => (
								<li key={goal}>{goal}</li>
							))}
						</ul>
					) : (
						<p className="meta">No goals yet.</p>
					)}
				</div>
				<div>
					<h2 className="section-label">Learning outcomes</h2>
					{course.outcomes.length > 0 ? (
						<ul>
							{course.outcomes.map((outcome) => (
								<li key={outcome}>{outcome}</li>
							))}
						</ul>
					) : (
						<p className="meta">No outcomes yet.</p>
					)}
				</div>
			</section>

			{editingDetails ? (
				<DetailsDialog
					course={course}
					saving={saving}
					error={error}
					onCancel={() => {
						setEditingDetails(false);
						setError(null);
					}}
					onSave={async (details) => {
						const done = await mutate(
							() => send("/api/course", "PATCH", details),
							"The course details could not be saved.",
						);
						if (done) setEditingDetails(false);
					}}
				/>
			) : null}
			{removing ? (
				<ConfirmDialog
					title="Remove this Lecture?"
					confirmLabel="Remove Lecture"
					busy={saving}
					error={error}
					onCancel={() => setRemoving(null)}
					onConfirm={() => {
						void mutate(
							() =>
								send(
									`/api/course/lectures/${encodeURIComponent(removing.id)}`,
									"DELETE",
								),
							"The Lecture could not be removed.",
						).then((done) => {
							if (done) setRemoving(null);
						});
					}}
				>
					<p>
						“{removing.title}” will be removed from the Course Plan. You can
						recover it from History.
					</p>
				</ConfirmDialog>
			) : null}
		</div>
	);
}

function RenameLecture({
	lecture,
	index,
	disabled,
	onCancel,
	onSave,
}: {
	lecture: Lecture;
	index: number;
	disabled: boolean;
	onCancel: () => void;
	onSave: (title: string) => Promise<void>;
}) {
	const [title, setTitle] = useState(lecture.title);
	return (
		<form
			className="plan-rename"
			onSubmit={(event) => {
				event.preventDefault();
				if (title.trim()) void onSave(title.trim());
			}}
		>
			<label htmlFor={`lecture-${lecture.id}`} className="visually-hidden">
				Lecture {index + 1} title
			</label>
			<input
				id={`lecture-${lecture.id}`}
				className="input"
				value={title}
				maxLength={200}
				aria-invalid={!title.trim()}
				// biome-ignore lint/a11y/noAutofocus: revealed by an explicit Rename action
				autoFocus
				onChange={(event) => setTitle(event.target.value)}
				onKeyDown={(event) => {
					if (event.key === "Escape") onCancel();
				}}
			/>
			<button type="button" className="btn" onClick={onCancel}>
				Cancel
			</button>
			<button
				type="submit"
				className="btn btn-primary"
				disabled={disabled || !title.trim()}
			>
				Save
			</button>
		</form>
	);
}

function DetailsDialog({
	course,
	saving,
	error,
	onCancel,
	onSave,
}: {
	course: CoursePlan;
	saving: boolean;
	error: string | null;
	onCancel: () => void;
	onSave: (details: {
		title: string;
		audience: string;
		goals: string[];
		outcomes: string[];
	}) => Promise<void>;
}) {
	const [title, setTitle] = useState(course.title);
	const [audience, setAudience] = useState(course.audience);
	const [goals, setGoals] = useState(course.goals.join("\n"));
	const [outcomes, setOutcomes] = useState(course.outcomes.join("\n"));
	const valid = title.trim() && audience.trim();
	return (
		<Dialog
			title="Edit course details"
			onClose={onCancel}
			dismissible={!saving}
			footer={
				<>
					<button
						type="button"
						className="btn"
						onClick={onCancel}
						disabled={saving}
					>
						Cancel
					</button>
					<button
						type="submit"
						form="course-details"
						className="btn btn-primary"
						disabled={!valid || saving}
					>
						Save details
					</button>
				</>
			}
		>
			<form
				id="course-details"
				className="plan-form"
				onSubmit={(event) => {
					event.preventDefault();
					if (!valid) return;
					void onSave({
						title: title.trim(),
						audience: audience.trim(),
						goals: lines(goals),
						outcomes: lines(outcomes),
					});
				}}
			>
				<div className="field">
					<label htmlFor="details-title">Course title</label>
					<input
						id="details-title"
						value={title}
						maxLength={200}
						required
						onChange={(event) => setTitle(event.target.value)}
					/>
				</div>
				<div className="field">
					<label htmlFor="details-audience">Audience</label>
					<textarea
						id="details-audience"
						rows={2}
						maxLength={1000}
						required
						value={audience}
						onChange={(event) => setAudience(event.target.value)}
					/>
				</div>
				<div className="field">
					<label htmlFor="details-goals">Goals</label>
					<textarea
						id="details-goals"
						rows={3}
						value={goals}
						aria-describedby="details-goals-help"
						onChange={(event) => setGoals(event.target.value)}
					/>
					<small id="details-goals-help">One per line.</small>
				</div>
				<div className="field">
					<label htmlFor="details-outcomes">Learning outcomes</label>
					<textarea
						id="details-outcomes"
						rows={3}
						value={outcomes}
						aria-describedby="details-outcomes-help"
						onChange={(event) => setOutcomes(event.target.value)}
					/>
					<small id="details-outcomes-help">
						What learners can do afterwards. One per line.
					</small>
				</div>
			</form>
			{error ? <Notice tone="error">{error}</Notice> : null}
		</Dialog>
	);
}

function ManualPlan({
	sources,
	onCreated,
	onAskAgent,
}: {
	sources: Source[];
	onCreated: (course: CoursePlan) => void;
	onAskAgent: (request: string, context?: AgentContext | null) => void;
}) {
	const [title, setTitle] = useState("");
	const [audience, setAudience] = useState("");
	const [lectureLines, setLectureLines] = useState("");
	const [goals, setGoals] = useState("");
	const [outcomes, setOutcomes] = useState("");
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);

	async function submit(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		setSaving(true);
		setError(null);
		try {
			onCreated(
				await send("/api/course", "POST", {
					title: title.trim(),
					audience: audience.trim(),
					goals: lines(goals),
					outcomes: lines(outcomes),
					lectures: lines(lectureLines).map((lecture) => ({ title: lecture })),
				}),
			);
		} catch (caught) {
			setError(errorMessage(caught, "The Course Plan could not be created."));
		} finally {
			setSaving(false);
		}
	}

	return (
		<div className="canvas-page plan-page">
			<header className="canvas-head">
				<div className="canvas-head-copy">
					<h1 className="display-title">Write the Course Plan</h1>
					<p className="meta">
						Or{" "}
						<button
							type="button"
							className="link-btn"
							onClick={() =>
								onAskAgent(
									sources.length > 0
										? "Draft a Course Plan from my Sources. Ask me about the audience and teaching aim if you need to."
										: "Help me draft a Course Plan. Ask me about the topic, audience, and teaching aim.",
									null,
								)
							}
						>
							ask the agent to draft it
						</button>
						{sources.length > 0
							? ` from your ${sources.length} Source${sources.length === 1 ? "" : "s"}.`
							: "."}
					</p>
				</div>
			</header>
			<form
				className="panel panel-pad plan-form"
				onSubmit={(event) => void submit(event)}
			>
				<div className="field">
					<label htmlFor="course-title">Course title</label>
					<input
						id="course-title"
						value={title}
						maxLength={200}
						required
						autoComplete="off"
						onChange={(event) => setTitle(event.target.value)}
					/>
				</div>
				<div className="field">
					<label htmlFor="course-audience">Audience</label>
					<textarea
						id="course-audience"
						rows={2}
						maxLength={1000}
						required
						value={audience}
						onChange={(event) => setAudience(event.target.value)}
					/>
				</div>
				<div className="field">
					<label htmlFor="course-lectures">Lectures in teaching order</label>
					<textarea
						id="course-lectures"
						rows={5}
						required
						value={lectureLines}
						aria-describedby="course-lectures-help"
						onChange={(event) => setLectureLines(event.target.value)}
					/>
					<small id="course-lectures-help">One Lecture title per line.</small>
				</div>
				<details className="plan-optional">
					<summary>Goals and learning outcomes (optional)</summary>
					<div className="field">
						<label htmlFor="course-goals">Goals</label>
						<textarea
							id="course-goals"
							rows={3}
							value={goals}
							onChange={(event) => setGoals(event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="course-outcomes">Learning outcomes</label>
						<textarea
							id="course-outcomes"
							rows={3}
							value={outcomes}
							onChange={(event) => setOutcomes(event.target.value)}
						/>
					</div>
				</details>
				{error ? <Notice tone="error">{error}</Notice> : null}
				<div className="form-actions">
					<button
						type="submit"
						className="btn btn-primary"
						disabled={
							saving ||
							!title.trim() ||
							!audience.trim() ||
							!lines(lectureLines).length
						}
					>
						Create Course Plan
					</button>
				</div>
			</form>
		</div>
	);
}
