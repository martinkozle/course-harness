import { CitationList, errorMessage, Markdown } from "@course-harness/ui";
import { Check, X } from "lucide-react";
import { useState } from "react";

import {
	ApiError,
	api,
	type LearnerQuestion,
	type LearnerQuiz,
	type OptionFeedback,
} from "./api";
import type { Identity } from "./identity";
import { Marks } from "./Marks";
import { type ProgressMap, questionKey, tally, tallySummary } from "./progress";

const KEYS = "ABCDEFGHIJ";

function Feedback({ id, feedback }: { id: string; feedback: OptionFeedback }) {
	return (
		<div
			id={id}
			className={`quiz-feedback ${feedback.correct ? "is-correct" : "is-wrong"}`}
		>
			{feedback.explanation ? <Markdown text={feedback.explanation} /> : null}
			{feedback.citations.length ? (
				<CitationList citations={feedback.citations} />
			) : null}
		</div>
	);
}

function Question({
	slug,
	lectureId,
	question,
	number,
	total,
	tried,
	identity,
	onAnswered,
	onStale,
}: {
	slug: string;
	lectureId: string;
	question: LearnerQuestion;
	number: number;
	total: number;
	tried: OptionFeedback[];
	identity: Identity;
	onAnswered: (questionId: string, feedback: OptionFeedback) => void;
	onStale: () => void;
}) {
	const [pending, setPending] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [announcement, setAnnouncement] = useState("");
	const byOption = new Map(tried.map((item) => [item.option_id, item]));
	const solved = tried.some((item) => item.correct);
	const headingId = `${question.id}-prompt`;

	async function pick(optionId: string) {
		setPending(optionId);
		setError(null);
		try {
			const result = await api.answer(slug, {
				lecture_id: lectureId,
				question_id: question.id,
				option_id: optionId,
				learner_name: identity.name,
				student_id: identity.studentId,
			});
			setAnnouncement(
				result.correct ? "Correct." : "Not this one. Read why, then try again.",
			);
			onAnswered(question.id, result);
		} catch (caught) {
			if (caught instanceof ApiError && caught.status === 422) {
				// The course moved to a newer Release in which this question changed.
				setError(
					"This question changed in a newer version of the course. The questions were reloaded.",
				);
				onStale();
			} else {
				setError(
					errorMessage(caught, "Your answer could not be sent. Try again."),
				);
			}
		} finally {
			setPending(null);
		}
	}

	return (
		<li className="quiz-question" aria-labelledby={headingId}>
			<p className="quiz-question-number">
				Question {number} of {total}
				{solved ? (
					<span className="quiz-solved">
						<Check aria-hidden="true" />
						Answered
					</span>
				) : null}
			</p>
			<div id={headingId} className="quiz-prompt">
				<Markdown text={question.prompt} />
			</div>
			<ul className="quiz-options">
				{question.options.map((option, index) => {
					const feedback = byOption.get(option.id);
					const state = feedback
						? feedback.correct
							? "correct"
							: "wrong"
						: "open";
					// Not `disabled`: a picked Option keeps keyboard focus and reads its feedback.
					const inactive = Boolean(feedback) || solved || pending !== null;
					const feedbackId = `${lectureId}-${option.id}-feedback`;
					const explained = Boolean(
						feedback && (feedback.explanation || feedback.citations.length),
					);
					return (
						<li key={option.id} className={`quiz-option is-${state}`}>
							<button
								type="button"
								className="quiz-choice"
								aria-disabled={inactive}
								aria-busy={pending === option.id}
								aria-describedby={explained ? feedbackId : undefined}
								onClick={() => {
									if (!inactive) pick(option.id);
								}}
							>
								<span className="quiz-key" aria-hidden="true">
									{state === "correct" ? (
										<Check />
									) : state === "wrong" ? (
										<X />
									) : (
										KEYS[index]
									)}
								</span>
								<span className="quiz-choice-text">{option.text}</span>
								{state !== "open" ? (
									<span className="quiz-verdict">
										{state === "correct" ? "Correct" : "Not this one"}
									</span>
								) : null}
							</button>
							{feedback && explained ? (
								<Feedback id={feedbackId} feedback={feedback} />
							) : null}
						</li>
					);
				})}
			</ul>
			{error ? (
				<p className="field-error" role="alert">
					{error}
				</p>
			) : null}
			<p className="visually-hidden" role="status">
				{announcement}
			</p>
		</li>
	);
}

/** A Lecture's Quiz: pick an Option, read why, and retry until correct. */
export function Quiz({
	slug,
	quiz,
	progress,
	identity,
	onAnswered,
	onStale,
}: {
	slug: string;
	quiz: LearnerQuiz;
	progress: ProgressMap;
	identity: Identity;
	onAnswered: (
		lectureId: string,
		questionId: string,
		feedback: OptionFeedback,
	) => void;
	/** Load the Quizzes again: the served Release no longer has a Question. */
	onStale: () => void;
}) {
	const counts = tally(quiz, progress);
	return (
		<div className="quiz">
			<p className="quiz-progress">
				<Marks tally={counts} />
				<span>{tallySummary(counts)}</span>
			</p>
			<ol className="quiz-questions">
				{quiz.questions.map((question, index) => (
					<Question
						key={question.id}
						slug={slug}
						lectureId={quiz.lecture_id}
						question={question}
						number={index + 1}
						total={quiz.questions.length}
						tried={
							progress.get(questionKey(quiz.lecture_id, question.id))?.tried ??
							[]
						}
						identity={identity}
						onAnswered={(questionId, feedback) =>
							onAnswered(quiz.lecture_id, questionId, feedback)
						}
						onStale={onStale}
					/>
				))}
			</ol>
		</div>
	);
}
