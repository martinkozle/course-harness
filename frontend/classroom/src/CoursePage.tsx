import { Tabs } from "@course-harness/ui";
import { ArrowRight } from "lucide-react";

import type { LectureSummary } from "./api";
import type { LearnerState } from "./CourseShell";
import { Marks } from "./Marks";
import { tally, tallySummary } from "./progress";
import {
	coursePath,
	exercisesPath,
	followLink,
	lecturePath,
	navigate,
	QUIZ_ANCHOR,
} from "./router";

type Tab = "lectures" | "exercises";

function plural(count: number, one: string, many: string) {
	return count === 1 ? `1 ${one}` : `${count} ${many}`;
}

/** Consecutive Lectures that share a group label, in course order. */
function grouped(lectures: LectureSummary[]) {
	const groups: {
		label: string | null;
		lectures: [number, LectureSummary][];
	}[] = [];
	lectures.forEach((lecture, index) => {
		const last = groups.at(-1);
		if (last && last.label === lecture.group)
			last.lectures.push([index + 1, lecture]);
		else
			groups.push({ label: lecture.group, lectures: [[index + 1, lecture]] });
	});
	return groups;
}

function QuizStatus({
	state,
	lectureId,
}: {
	state: LearnerState;
	lectureId: string;
}) {
	const quiz = state.quizzes?.find((item) => item.lecture_id === lectureId);
	if (!quiz) return null;
	const counts = tally(quiz, state.progress);
	return (
		<span className="quiz-status">
			<Marks tally={counts} />
			<span>{tallySummary(counts)}</span>
		</span>
	);
}

function LectureList({ state }: { state: LearnerState }) {
	const { course } = state;
	return (
		<div className="lecture-groups">
			{grouped(course.lectures).map((group, index) => (
				<section
					// biome-ignore lint/suspicious/noArrayIndexKey: groups are positional runs of Lectures
					key={index}
					className="lecture-group"
					aria-label={group.label ?? undefined}
				>
					{group.label ? <h3 className="group-label">{group.label}</h3> : null}
					<ol className="lecture-list">
						{group.lectures.map(([number, lecture]) => (
							<li key={lecture.id}>
								<a
									className="lecture-row"
									href={lecturePath(course.slug, lecture.id)}
									onClick={followLink}
								>
									<span className="lecture-number" aria-hidden="true">
										{number}
									</span>
									<span className="lecture-text">
										<span className="lecture-title">{lecture.title}</span>
										<span className="meta">
											{plural(lecture.slide_count, "slide", "slides")}
											{lecture.question_count
												? ` · ${plural(lecture.question_count, "question", "questions")}`
												: ""}
										</span>
									</span>
									<QuizStatus state={state} lectureId={lecture.id} />
								</a>
							</li>
						))}
					</ol>
				</section>
			))}
		</div>
	);
}

function Exercises({ state }: { state: LearnerState }) {
	const { course, quizzes } = state;
	if (!quizzes) return <p className="meta">Loading…</p>;
	if (!quizzes.length)
		return <p className="meta">This course has no questions yet.</p>;
	const numbers = new Map(
		course.lectures.map((lecture, index) => [lecture.id, index + 1]),
	);
	return (
		<ol className="exercise-list">
			{quizzes.map((quiz) => {
				const counts = tally(quiz, state.progress);
				const action =
					counts.answered === 0
						? "Start"
						: counts.correct === counts.total
							? "Review"
							: "Continue";
				return (
					<li key={quiz.lecture_id} className="exercise-row">
						<div className="exercise-text">
							<p className="eyebrow">Lecture {numbers.get(quiz.lecture_id)}</p>
							<h3>{quiz.lecture_title}</h3>
							<p className="quiz-status">
								<Marks tally={counts} />
								<span>{tallySummary(counts)}</span>
							</p>
						</div>
						<a
							className={`btn ${action === "Review" ? "" : "btn-primary"}`}
							href={`${lecturePath(course.slug, quiz.lecture_id)}#${QUIZ_ANCHOR}`}
							onClick={followLink}
							aria-label={`${action}: ${quiz.lecture_title}`}
						>
							{action}
							<ArrowRight aria-hidden="true" />
						</a>
					</li>
				);
			})}
		</ol>
	);
}

/** A course's Lectures in order, and its Exercises: every Quiz with the student's progress. */
export function CoursePage({ tab, state }: { tab: Tab; state: LearnerState }) {
	const { course, quizzes } = state;
	const questions = course.lectures.reduce(
		(sum, lecture) => sum + lecture.question_count,
		0,
	);
	const solved = quizzes
		? quizzes.reduce(
				(sum, quiz) => sum + tally(quiz, state.progress).correct,
				0,
			)
		: 0;
	return (
		<article className="course">
			<header className="course-header">
				<p className="eyebrow">
					{course.release ? course.release.name : "No Release yet"}
					{course.role === "instructor"
						? " · You are viewing as an Instructor"
						: ""}
				</p>
				<h2 className="display-title">{course.title}</h2>
				<p className="meta">
					{plural(course.lectures.length, "Lecture", "Lectures")}
					{questions
						? ` · ${solved} of ${plural(questions, "question", "questions")} answered correctly`
						: ""}
				</p>
			</header>
			{course.lectures.length ? (
				<>
					<Tabs
						label="Course"
						tabs={[
							{
								id: "lectures",
								label: "Lectures",
								count: course.lectures.length,
							},
							{
								id: "exercises",
								label: "Exercises",
								count: course.lectures.filter((lecture) => lecture.has_quiz)
									.length,
							},
						]}
						value={tab}
						onChange={(next: Tab) =>
							navigate(
								next === "lectures"
									? coursePath(course.slug)
									: exercisesPath(course.slug),
							)
						}
					/>
					{tab === "lectures" ? (
						<LectureList state={state} />
					) : (
						<Exercises state={state} />
					)}
				</>
			) : (
				<p className="meta">
					This course has no Lectures yet. Check back after its next Release.
				</p>
			)}
		</article>
	);
}
