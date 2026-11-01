import { Dialog, errorMessage, Notice } from "@course-harness/ui";
import { UserRound } from "lucide-react";
import {
	type ReactNode,
	useCallback,
	useEffect,
	useId,
	useMemo,
	useRef,
	useState,
} from "react";

import {
	ApiError,
	api,
	type CourseSummary,
	type LearnerQuiz,
	type OptionFeedback,
	type Role,
} from "./api";
import { CoursePage } from "./CoursePage";
import { IdentityForm } from "./IdentityForm";
import { InsightsPage } from "./InsightsPage";
import { type Identity, useIdentity } from "./identity";
import { LecturePage } from "./LecturePage";
import { type ProgressMap, progressMap, withAnswer } from "./progress";
import {
	type CourseView,
	coursePath,
	followLink,
	insightsPath,
	navigate,
} from "./router";

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
	reloadQuizzes: () => void;
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
	/** Load the summary again in place, for instance after a sync switched Releases. */
	const refresh = useCallback(() => {
		api
			.course(slug)
			.then(setCourse)
			.catch(() => {
				// Keep showing the summary already loaded.
			});
	}, [slug]);
	return { course, error, refresh };
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
	// Changes when the served Release no longer has a Question on the page, to load again.
	const [generation, setGeneration] = useState(0);
	const loadedFor = useRef<string | null>(null);
	const hasRelease = Boolean(course?.release);
	// biome-ignore lint/correctness/useExhaustiveDependencies: `generation` asks for a reload
	useEffect(() => {
		if (!studentId || !hasRelease) return;
		let active = true;
		setError(null);
		if (loadedFor.current !== studentId) {
			// Another student's Option order and progress must never show, even briefly.
			loadedFor.current = studentId;
			setQuizzes(null);
			setProgress(new Map());
		}
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
	}, [slug, studentId, hasRelease, generation]);
	const onAnswered = useCallback(
		(lectureId: string, questionId: string, feedback: OptionFeedback) =>
			setProgress((current) =>
				withAnswer(current, lectureId, questionId, feedback),
			),
		[],
	);
	const reloadQuizzes = useCallback(
		() => setGeneration((value) => value + 1),
		[],
	);
	return { quizzes, progress, error, onAnswered, reloadQuizzes };
}

const VIEW_KEY = (slug: string) => `classroom:view-as:${slug}`;

/**
 * Which role's pages an Instructor is looking at. It is a preference of this browser tab only:
 * the server still treats every request as an Instructor's, so answers given while viewing as a
 * Learner are recorded as an Instructor's and left out of Insights by default.
 */
function useViewAs(slug: string) {
	const [viewAs, setViewAs] = useState<Role>(() => {
		try {
			return sessionStorage.getItem(VIEW_KEY(slug)) === "learner"
				? "learner"
				: "instructor";
		} catch {
			return "instructor";
		}
	});
	const choose = useCallback(
		(role: Role) => {
			setViewAs(role);
			try {
				sessionStorage.setItem(VIEW_KEY(slug), role);
			} catch {
				// The choice lasts for this page only.
			}
		},
		[slug],
	);
	return [viewAs, choose] as const;
}

const VIEW_SWITCH_DESCRIPTION =
	"Your answers still count as an Instructor’s and stay out of Insights.";

/** The Instructor | Learner switch; only shown to people the server lets in as Instructors. */
function ViewSwitch({
	viewAs,
	onChange,
}: {
	viewAs: Role;
	onChange: (role: Role) => void;
}) {
	const descriptionId = useId();
	return (
		<fieldset
			className="view-switch"
			aria-describedby={descriptionId}
			title={VIEW_SWITCH_DESCRIPTION}
		>
			<legend className="visually-hidden">Viewing as</legend>
			<span id={descriptionId} className="visually-hidden">
				{VIEW_SWITCH_DESCRIPTION}
			</span>
			<span className="view-switch-label" aria-hidden="true">
				Viewing as
			</span>
			<span className="view-switch-options">
				{(["instructor", "learner"] as const).map((role) => (
					<button
						key={role}
						type="button"
						aria-pressed={viewAs === role}
						onClick={() => onChange(role)}
					>
						{role === "instructor" ? "Instructor" : "Learner"}
					</button>
				))}
			</span>
		</fieldset>
	);
}

/** A course's pages, with its title and the student's identity in the bar above them. */
export function CourseShell({
	slug,
	view,
}: {
	slug: string;
	view: CourseView;
}) {
	const { course: served, error, refresh: refreshCourse } = useCourse(slug);
	const [identity, setIdentity] = useIdentity();
	const [editing, setEditing] = useState(false);
	const [chosenView, setViewAs] = useViewAs(slug);
	// Insights are always seen as an Instructor.
	const viewAs: Role = view.view === "insights" ? "instructor" : chosenView;
	const instructor = served?.role === "instructor";
	// Viewing as a Learner shows exactly the Learner pages; the server's role is unchanged.
	const course = useMemo(
		() =>
			served && instructor && viewAs === "learner"
				? { ...served, role: "learner" as const }
				: served,
		[served, instructor, viewAs],
	);
	const learning = useLearning(slug, course, identity?.studentId ?? null);

	function switchView(role: Role) {
		setViewAs(role);
		if (role === "learner" && view.view === "insights")
			navigate(coursePath(slug));
	}

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
	} else if (view.view === "insights") {
		// Insights need no student identity: they are about everyone's answers.
		content =
			course.role === "instructor" ? (
				<InsightsPage course={course} onSynced={refreshCourse} />
			) : (
				<Notice tone="info">
					Insights are for the course’s Instructors. Your Lectures and Exercises
					are on the course page.
				</Notice>
			);
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
				{course.role === "instructor" ? (
					<Notice tone="info">
						{course.administrator
							? "You are signed in as the Administrator, so you see this course as an Instructor."
							: "You opened an Instructor link."}{" "}
						<a href={insightsPath(slug)} onClick={followLink}>
							Open Insights
						</a>{" "}
						to see where Learners get stuck, or enter a name and student ID to
						try the course. Switch “Viewing as” to Learner to see exactly what
						Learners see. Your own answers are left out of Insights unless you
						include them.
					</Notice>
				) : null}
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
			reloadQuizzes: learning.reloadQuizzes,
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
				<div className="bar-actions">
					{course && instructor ? (
						<ViewSwitch viewAs={viewAs} onChange={switchView} />
					) : null}
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
				</div>
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
