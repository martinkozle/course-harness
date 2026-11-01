import { ListChecks } from "lucide-react";

import type { CoursePlan, QuizSummary } from "../models";
import type { AgentContext } from "../useCourseAgent";

/** Every Lecture's Quiz in teaching order, with a way to open each one. */
export function ExercisesCanvas({
	course,
	quizzes,
	busy,
	onOpenQuiz,
	onAskAgent,
}: {
	course: CoursePlan | null;
	quizzes: QuizSummary[];
	busy: boolean;
	onOpenQuiz: (lectureId: string) => void;
	onAskAgent: (request: string, context?: AgentContext | null) => void;
}) {
	const questions = new Map(
		quizzes.map((quiz) => [quiz.lecture_id, quiz.question_count]),
	);
	const lectures = (course?.lectures ?? []).map((lecture, index) => ({
		lecture,
		number: index + 1,
		count: questions.get(lecture.id) ?? 0,
	}));
	const withQuiz = lectures.filter((item) => item.count > 0);
	const withoutQuiz = lectures.filter((item) => item.count === 0);
	const total = withQuiz.reduce((sum, item) => sum + item.count, 0);

	return (
		<div className="canvas-page exercises-page">
			<header className="canvas-head">
				<div className="canvas-head-copy">
					<h1 className="display-title">Exercises</h1>
					<p className="meta">
						{withQuiz.length === 0
							? "No Quizzes yet. Each Lecture can end with a Quiz that Learners take after it."
							: `${withQuiz.length} Quiz${withQuiz.length === 1 ? "" : "zes"} · ${total} Question${total === 1 ? "" : "s"}`}
					</p>
				</div>
			</header>

			{withQuiz.length > 0 ? (
				<section
					className="canvas-section"
					aria-labelledby="exercises-quizzes-heading"
				>
					<div className="canvas-section-head">
						<h2 id="exercises-quizzes-heading">Quizzes</h2>
					</div>
					<ol className="row-list" aria-label="Quizzes">
						{withQuiz.map(({ lecture, number, count }) => (
							<li key={lecture.id} className="row plan-lecture">
								<span className="plan-number" aria-hidden="true">
									{number}
								</span>
								<div className="row-main">
									<button
										type="button"
										className="row-title plan-lecture-title"
										onClick={() => onOpenQuiz(lecture.id)}
									>
										{lecture.title}
									</button>
									<span className="row-meta">
										<ListChecks
											aria-hidden="true"
											className="plan-slides-icon"
										/>
										{count} Question{count === 1 ? "" : "s"}
									</span>
								</div>
								<div className="row-actions">
									<button
										type="button"
										className="btn btn-small"
										aria-label={`Open the Quiz for ${lecture.title}`}
										onClick={() => onOpenQuiz(lecture.id)}
									>
										Open Quiz
									</button>
								</div>
							</li>
						))}
					</ol>
				</section>
			) : null}

			{withoutQuiz.length > 0 ? (
				<section
					className="canvas-section"
					aria-labelledby="exercises-missing-heading"
				>
					<div className="canvas-section-head">
						<h2 id="exercises-missing-heading">Lectures without a Quiz</h2>
					</div>
					<ol className="row-list" aria-label="Lectures without a Quiz">
						{withoutQuiz.map(({ lecture, number }) => (
							<li key={lecture.id} className="row plan-lecture">
								<span className="plan-number" aria-hidden="true">
									{number}
								</span>
								<div className="row-main">
									<span className="row-title plan-lecture-title">
										{lecture.title}
									</span>
								</div>
								<div className="row-actions">
									<button
										type="button"
										className="btn btn-quiet btn-small"
										aria-label={`Write a Quiz for ${lecture.title}`}
										disabled={busy}
										onClick={() =>
											onAskAgent(
												`Write a Quiz for “${lecture.title}” grounded in the course Sources.`,
												{
													key: `quiz-${lecture.id}`,
													label: `Lecture ${number} · ${lecture.title}`,
													instruction: `I want a Quiz for the Lecture "${lecture.title}"`,
												},
											)
										}
									>
										Write a Quiz
									</button>
								</div>
							</li>
						))}
					</ol>
				</section>
			) : null}
		</div>
	);
}
