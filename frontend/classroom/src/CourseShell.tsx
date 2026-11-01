import { Dialog, errorMessage, Notice } from "@course-harness/ui";
import { UserRound } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useState } from "react";

import {
	ApiError,
	api,
	type CourseSummary,
	type LearnerQuiz,
	type OptionFeedback,
} from "./api";
import { CoursePage } from "./CoursePage";
import { IdentityForm } from "./IdentityForm";
import { type Identity, useIdentity } from "./identity";
import { LecturePage } from "./LecturePage";
import { type ProgressMap, progressMap, withAnswer } from "./progress";
import { type CourseView, coursePath, followLink } from "./router";

export type LearnerState = {
	course: CourseSummary;
	identity: Identity;
	quizzes: LearnerQuiz[] | null;
	progress: ProgressMap;
	onAnswered: (
		lectureId: string,
		questionId: string,
		feedback: OptionFeedback,
	) => void;
};

function useCourse(slug: string) {
	const [course, setCourse] = useState<CourseSummary | null>(null);
	const [error, setError] = useState<{
		message: string;
		noAccess: boolean;
	} | null>(null);
	useEffect(() => {
		let active = true;
		setCourse(null);
		setError(null);
		api
			.course(slug)
			.then((summary) => {
				if (active) setCourse(summary);
			})
			.catch((caught) => {
				if (!active) return;
				setError({
					message: errorMessage(caught, "This course could not be loaded."),
					noAccess: caught instanceof ApiError && caught.status === 401,
				});
			});
		return () => {
			active = false;
		};
	}, [slug]);
	return { course, error };
}

/** The student's Quizzes and progress, loaded again whenever their student ID changes. */
function useLearning(
	slug: string,
	course: CourseSummary | null,
	studentId: string | null,
) {
	const [quizzes, setQuizzes] = useState<LearnerQuiz[] | null>(null);
	const [progress, setProgress] = useState<ProgressMap>(new Map());
	const [error, setError] = useState<string | null>(null);
	const hasRelease = Boolean(course?.release);
	useEffect(() => {
		if (!studentId || !hasRelease) return;
		let active = true;
		setError(null);
		Promise.all([api.quizzes(slug, studentId), api.progress(slug, studentId)])
			.then(([loaded, restored]) => {
				if (!active) return;
				setQuizzes(loaded);
				setProgress(progressMap(restored));
			})
			.catch((caught) => {
				if (active)
					setError(errorMessage(caught, "Your progress could not be loaded."));
			});
		return () => {
			active = false;
		};
	}, [slug, studentId, hasRelease]);
	const onAnswered = useCallback(
		(lectureId: string, questionId: string, feedback: OptionFeedback) =>
			setProgress((current) =>
				withAnswer(current, lectureId, questionId, feedback),
			),
		[],
	);
	return { quizzes, progress, error, onAnswered };
}

/** A course's pages, with its title and the student's identity in the bar above them. */
export function CourseShell({
	slug,
	view,
}: {
	slug: string;
	view: CourseView;
}) {
	const { course, error } = useCourse(slug);
	const [identity, setIdentity] = useIdentity();
	const [editing, setEditing] = useState(false);
	const learning = useLearning(slug, course, identity?.studentId ?? null);

	useEffect(() => {
		// A Lecture page sets its own title.
		if (course && view.view !== "lecture")
			document.title = `${course.title} · Classroom`;
	}, [course, view.view]);

	let content: ReactNode;
	if (error) {
		content = (
			<Notice tone={error.noAccess ? "info" : "error"}>
				{error.noAccess
					? "To see this course, open the link you were given. If it no longer works, ask for a new one."
					: error.message}
			</Notice>
		);
	} else if (!course) {
		content = <p className="meta">Loading…</p>;
	} else if (!identity) {
		content = (
			<section className="welcome" aria-labelledby="welcome-title">
				<p className="eyebrow">{course.title}</p>
				<h2 id="welcome-title" className="display-title">
					Before you start
				</h2>
				<p className="welcome-copy">
					Your name and student ID go with your answers, so your progress
					follows you to other devices and the course’s Instructors can see
					where you are.
				</p>
				<IdentityForm
					initial={null}
					submitLabel="Start"
					onSubmit={setIdentity}
				/>
			</section>
		);
	} else {
		const state: LearnerState = {
			course,
			identity,
			quizzes: learning.quizzes,
			progress: learning.progress,
			onAnswered: learning.onAnswered,
		};
		content = (
			<>
				{learning.error ? <Notice tone="error">{learning.error}</Notice> : null}
				{view.view === "lecture" ? (
					<LecturePage
						key={view.lectureId}
						lectureId={view.lectureId}
						state={state}
					/>
				) : (
					<CoursePage tab={view.view} state={state} />
				)}
			</>
		);
	}

	return (
		<div className="classroom">
			<header className="classroom-bar">
				{course ? (
					<a className="bar-title" href={coursePath(slug)} onClick={followLink}>
						{course.title}
					</a>
				) : (
					<span className="bar-title">Classroom</span>
				)}
				{identity && course ? (
					<button
						type="button"
						className="identity-chip"
						onClick={() => setEditing(true)}
						title="Change your name or student ID"
					>
						<UserRound aria-hidden="true" />
						<span className="identity-name">{identity.name}</span>
						<span className="identity-id">{identity.studentId}</span>
					</button>
				) : null}
			</header>
			<main className="classroom-main">{content}</main>
			{editing && identity ? (
				<Dialog
					title="Your name and student ID"
					onClose={() => setEditing(false)}
				>
					<p className="meta">
						Your progress is kept by student ID. Change it only if you typed it
						wrong; answers you gave under the old ID stay with it.
					</p>
					<IdentityForm
						initial={identity}
						submitLabel="Save"
						onSubmit={(next) => {
							setIdentity(next);
							setEditing(false);
						}}
						onCancel={() => setEditing(false)}
					/>
				</Dialog>
			) : null}
		</div>
	);
}
