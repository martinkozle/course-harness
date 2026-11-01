import { errorMessage, Notice } from "@course-harness/ui";
import { Download, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
	answersCsvUrl,
	api,
	type CourseSummary,
	type CourseSync,
	type Insights,
	type InsightsRelease,
	type QuestionInsight,
} from "./api";
import { CourseTabs } from "./CoursePage";

const SYNC_POLL_MS = 1_500;

const percent = (value: number | null) =>
	value === null ? "–" : `${Math.round(value * 100)}%`;

const decimal = (value: number) =>
	value.toLocaleString(undefined, { maximumFractionDigits: 2 });

function plural(count: number, one: string, many: string) {
	return count === 1 ? `1 ${one}` : `${count} ${many}`;
}

function when(iso: string | null) {
	if (!iso) return "–";
	const date = new Date(iso);
	return Number.isNaN(date.getTime())
		? iso
		: date.toLocaleString(undefined, {
				dateStyle: "medium",
				timeStyle: "short",
			});
}

/** A Question's Markdown prompt as one line of plain text, for a table cell. */
function plainPrompt(markdown: string) {
	return markdown
		.replace(/```[\s\S]*?```/g, " … ")
		.replace(/[*_`#>[\]]/g, "")
		.replace(/\s+/g, " ")
		.trim();
}

function releaseLabel(release: InsightsRelease) {
	return release.name ?? release.tag.replace(/^course-release\//, "");
}

/** Questions where Learners struggle most first: lowest first-try correct rate, unanswered last. */
function stuckFirst(questions: QuestionInsight[]) {
	return [...questions].sort((a, b) => {
		const left = a.first_try_correct_rate ?? Number.POSITIVE_INFINITY;
		const right = b.first_try_correct_rate ?? Number.POSITIVE_INFINITY;
		return left - right || a.number - b.number;
	});
}

function changeNotes(question: QuestionInsight) {
	const { changes } = question;
	const notes: string[] = [];
	if (changes.answer_key_changed)
		notes.push(
			`${plural(changes.answer_key_changed, "answer", "answers")} given when another Option was correct`,
		);
	if (changes.question_changed)
		notes.push(
			`${plural(changes.question_changed, "answer", "answers")} given to an earlier wording`,
		);
	if (changes.option_removed)
		notes.push(
			`${plural(changes.option_removed, "answer", "answers")} picked an Option this Release no longer has`,
		);
	if (changes.release_unavailable)
		notes.push(
			`${plural(changes.release_unavailable, "answer", "answers")} from a Release that cannot be read`,
		);
	if (!question.gradable)
		notes.push("This Question has no single correct Option");
	return notes;
}

/** A first-try correct rate as a thin bar on a 0–100% track, with its value as text. */
function RateBar({ value }: { value: number | null }) {
	if (value === null) return <span className="meta">No answers</span>;
	return (
		<span className="rate">
			<span className="rate-track" aria-hidden="true">
				<span
					className="rate-fill"
					style={{ width: `${value * 100}%` }}
				/>
			</span>
			<span className="rate-value">{percent(value)}</span>
		</span>
	);
}

function useSync(slug: string, onSynced: () => void) {
	const [sync, setSync] = useState<CourseSync | null>(null);
	const [error, setError] = useState<string | null>(null);
	const onSyncedRef = useRef(onSynced);
	onSyncedRef.current = onSynced;
	const syncing = sync?.syncing ?? false;

	useEffect(() => {
		let active = true;
		api
			.syncStatus(slug)
			.then((status) => {
				if (active) setSync(status);
			})
			.catch(() => {
				// The status line stays empty; Sync now still works.
			});
		return () => {
			active = false;
		};
	}, [slug]);

	useEffect(() => {
		if (!syncing) return;
		const timer = window.setInterval(() => {
			api
				.syncStatus(slug)
				.then((status) => {
					setSync(status);
					if (!status.syncing) onSyncedRef.current();
				})
				.catch((caught) =>
					setError(errorMessage(caught, "The sync status could not be read.")),
				);
		}, SYNC_POLL_MS);
		return () => window.clearInterval(timer);
	}, [slug, syncing]);

	async function start() {
		setError(null);
		try {
			const status = await api.syncNow(slug);
			setSync({ ...status, syncing: true });
		} catch (caught) {
			setError(errorMessage(caught, "The sync could not be started."));
		}
	}
	return { sync, error, start };
}

function SyncStatusLine({ sync }: { sync: CourseSync | null }) {
	if (!sync) return null;
	if (sync.syncing)
		return (
			<span className="meta" role="status">
				Checking the repository for a new Release…
			</span>
		);
	const outcome =
		sync.sync_status === "error"
			? "Last sync failed"
			: sync.sync_status === "warning"
				? "Last sync finished with warnings"
				: "Last synced";
	return (
		<span
			className={`meta ${sync.sync_status === "error" ? "sync-failed" : ""}`}
			role="status"
			title={sync.sync_message ?? undefined}
		>
			{outcome} {when(sync.last_sync_at)}
			{sync.release ? ` · serving ${sync.release.name}` : ""}
		</span>
	);
}

function Overview({ insights }: { insights: Insights }) {
	const { overview } = insights;
	const tiles = [
		{ label: "Learners", value: String(overview.learners) },
		{ label: "Questions", value: String(overview.questions) },
		{ label: "Answers", value: String(overview.answers) },
		{
			label: "Average score",
			value: percent(overview.mean_score),
			detail:
				overview.average_total_score === null
					? "per answered question"
					: `${decimal(overview.average_total_score)} of ${overview.questions} points per Learner`,
		},
	];
	return (
		<dl className="stat-tiles">
			{tiles.map((tile) => (
				<div key={tile.label} className="stat-tile">
					<dt>{tile.label}</dt>
					<dd>
						<span className="stat-value">{tile.value}</span>
						{tile.detail ? (
							<span className="stat-detail">{tile.detail}</span>
						) : null}
					</dd>
				</div>
			))}
		</dl>
	);
}

function QuestionsTable({ insights }: { insights: Insights }) {
	const questions = stuckFirst(insights.questions);
	if (!questions.length)
		return <p className="meta">This Release has no questions.</p>;
	return (
		<div className="table-scroll">
			<table className="insights-table">
				<thead>
					<tr>
						<th scope="col">Question</th>
						<th scope="col" className="numeric">
							Learners
						</th>
						<th scope="col">First try correct</th>
						<th scope="col" className="numeric">
							Mean score
						</th>
						<th scope="col">Most-chosen wrong option</th>
					</tr>
				</thead>
				<tbody>
					{questions.map((question) => {
						const notes = changeNotes(question);
						return (
							<tr key={`${question.lecture_id}:${question.question_id}`}>
								<th scope="row" className="question-cell">
									<span className="question-prompt">
										<span className="question-number">Q{question.number}</span>{" "}
										{plainPrompt(question.prompt)}
									</span>
									<span className="meta">
										{question.lecture_title} ·{" "}
										{plural(question.attempts, "answer", "answers")}
										{question.correct_option_text
											? ` · correct: ${question.correct_option_text}`
											: ""}
									</span>
									{notes.length ? (
										<span className="change-note">{notes.join("; ")}</span>
									) : null}
								</th>
								<td className="numeric">{question.learners}</td>
								<td>
									<RateBar value={question.first_try_correct_rate} />
								</td>
								<td className="numeric">{percent(question.mean_score)}</td>
								<td>
									{question.most_chosen_wrong ? (
										<span className="wrong-option">
											<span>{question.most_chosen_wrong.text}</span>
											<span className="meta">
												{plural(
													question.most_chosen_wrong.learners,
													"Learner",
													"Learners",
												)}
											</span>
										</span>
									) : (
										<span className="meta">–</span>
									)}
								</td>
							</tr>
						);
					})}
				</tbody>
			</table>
		</div>
	);
}

function StudentsTable({ insights }: { insights: Insights }) {
	if (!insights.students.length)
		return <p className="meta">No Learner has answered yet.</p>;
	return (
		<div className="table-scroll">
			<table className="insights-table">
				<thead>
					<tr>
						<th scope="col">Name</th>
						<th scope="col">Student ID</th>
						<th scope="col" className="numeric">
							Attempts
						</th>
						<th scope="col" className="numeric">
							Answered
						</th>
						<th scope="col" className="numeric">
							Correct
						</th>
						<th scope="col" className="numeric">
							Score
						</th>
						<th scope="col">Last activity</th>
					</tr>
				</thead>
				<tbody>
					{insights.students.map((student) => (
						<tr key={student.student_id}>
							<th scope="row">
								{student.name}
								{student.role === "instructor" ? (
									<span className="meta"> · Instructor</span>
								) : null}
							</th>
							<td className="mono">{student.student_id}</td>
							<td className="numeric">{student.attempts}</td>
							<td className="numeric">{student.questions_answered}</td>
							<td className="numeric">{student.correct}</td>
							<td className="numeric">
								{decimal(student.total_score)}
								<span className="meta"> / {insights.overview.questions}</span>
							</td>
							<td>{when(student.last_activity)}</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

function ReleasesTable({ insights }: { insights: Insights }) {
	return (
		<div className="table-scroll">
			<table className="insights-table">
				<thead>
					<tr>
						<th scope="col">Release</th>
						<th scope="col" className="numeric">
							Answers
						</th>
						<th scope="col" className="numeric">
							Learners
						</th>
						<th scope="col">Last answer</th>
					</tr>
				</thead>
				<tbody>
					{insights.releases.map((release) => (
						<tr key={release.tag}>
							<th scope="row">
								{releaseLabel(release)}
								<span className="meta">
									{[
										release.served ? "served now" : null,
										release.graded ? "graded against" : null,
										release.readable ? null : "cannot be read",
									]
										.filter(Boolean)
										.map((label) => ` · ${label}`)
										.join("")}
								</span>
							</th>
							<td className="numeric">{release.answers}</td>
							<td className="numeric">{release.learners}</td>
							<td>{when(release.last_answer_at)}</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

/** An Instructor's view of where Learners get stuck, graded against one Release. */
export function InsightsPage({ course }: { course: CourseSummary }) {
	const [release, setRelease] = useState<string | null>(null);
	const [includeInstructors, setIncludeInstructors] = useState(false);
	const [insights, setInsights] = useState<Insights | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [reload, setReload] = useState(0);
	const releaseId = useId();
	const instructorsId = useId();

	// biome-ignore lint/correctness/useExhaustiveDependencies: `reload` asks for the same Insights again after a sync
	useEffect(() => {
		if (!course.release) return;
		let active = true;
		setError(null);
		api
			.insights(course.slug, { release, includeInstructors })
			.then((loaded) => {
				if (active) setInsights(loaded);
			})
			.catch((caught) => {
				if (active)
					setError(errorMessage(caught, "Insights could not be loaded."));
			});
		return () => {
			active = false;
		};
	}, [course.slug, course.release, release, includeInstructors, reload]);

	const reloadInsights = useCallback(() => setReload((count) => count + 1), []);
	const sync = useSync(course.slug, reloadInsights);

	const options = { release, includeInstructors };
	const hidden = insights?.overview.hidden_instructor_answers ?? 0;

	return (
		<article className="course insights">
			<header className="course-header">
				<p className="eyebrow">Insights for Instructors</p>
				<h2 className="display-title">{course.title}</h2>
				<p className="meta">
					Where Learners get stuck, from every answer they gave. Scores are
					worked out against the Release you choose, so a fixed answer key
					regrades earlier answers.
				</p>
			</header>
			<CourseTabs course={course} tab="insights" />
			<div className="insights-toolbar">
				<div className="field">
					<label htmlFor={releaseId}>Grade against</label>
					<select
						id={releaseId}
						value={release ?? ""}
						onChange={(event) => setRelease(event.target.value || null)}
						disabled={!insights}
					>
						<option value="">
							{course.release
								? `${course.release.name} (served now)`
								: "The served Release"}
						</option>
						{insights?.releases
							.filter((item) => !item.served && item.readable)
							.map((item) => (
								<option key={item.tag} value={item.tag}>
									{releaseLabel(item)}
								</option>
							))}
					</select>
				</div>
				<label className="check-row" htmlFor={instructorsId}>
					<input
						id={instructorsId}
						type="checkbox"
						checked={includeInstructors}
						onChange={(event) => setIncludeInstructors(event.target.checked)}
					/>
					Include answers given with the Instructor link
				</label>
				<div className="insights-actions">
					<a
						className="btn"
						href={answersCsvUrl(course.slug, options)}
						download
					>
						<Download aria-hidden="true" />
						Download CSV
					</a>
					<button
						type="button"
						className="btn"
						onClick={sync.start}
						disabled={sync.sync?.syncing}
					>
						<RefreshCw aria-hidden="true" />
						Sync now
					</button>
				</div>
			</div>
			<div className="sync-line">
				<SyncStatusLine sync={sync.sync} />
				{sync.error ? <Notice tone="error">{sync.error}</Notice> : null}
			</div>
			{!course.release ? (
				<p className="meta">
					This course has no Release yet, so there is nothing to show.
				</p>
			) : error ? (
				<Notice tone="error">{error}</Notice>
			) : !insights ? (
				<p className="meta">Loading…</p>
			) : (
				<>
					<Overview insights={insights} />
					{hidden ? (
						<p className="meta">
							{plural(hidden, "answer", "answers")} given with the Instructor
							link {hidden === 1 ? "is" : "are"} left out.
						</p>
					) : null}
					<section className="insights-section" aria-labelledby="stuck-title">
						<h3 id="stuck-title">Where Learners get stuck</h3>
						<p className="meta">
							Questions with the fewest Learners right on their first try come
							first. Graded against {releaseLabel(insights.graded_release)}.
						</p>
						<QuestionsTable insights={insights} />
						{insights.removed_questions.length ? (
							<Notice tone="info">
								{plural(
									insights.removed_questions.length,
									"question that has answers is",
									"questions that have answers are",
								)}{" "}
								not part of {releaseLabel(insights.graded_release)}:{" "}
								{insights.removed_questions
									.map(
										(item) =>
											`${item.prompt ? plainPrompt(item.prompt) : item.question_id} (${plural(item.answers, "answer", "answers")})`,
									)
									.join("; ")}
								.
							</Notice>
						) : null}
					</section>
					<section
						className="insights-section"
						aria-labelledby="students-title"
					>
						<h3 id="students-title">Learners</h3>
						<StudentsTable insights={insights} />
					</section>
					<section
						className="insights-section"
						aria-labelledby="releases-title"
					>
						<h3 id="releases-title">Answers by Release</h3>
						<ReleasesTable insights={insights} />
					</section>
				</>
			)}
		</article>
	);
}
