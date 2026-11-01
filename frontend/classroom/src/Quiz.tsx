import { CitationList, errorMessage, Markdown } from "@course-harness/ui";
import { Check, X } from "lucide-react";
import { useState } from "react";

import {
	api,
	type LearnerQuestion,
	type LearnerQuiz,
	type OptionFeedback,
} from "./api";
import type { Identity } from "./identity";
import { Marks } from "./Marks";
import { type ProgressMap, tally, tallySummary } from "./progress";

const KEYS = "ABCDEFGHIJ";

function Feedback({ feedback }: { feedback: OptionFeedback }) {
	return (
		<div
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
}: {
	slug: string;
	lectureId: string;
	question: LearnerQuestion;
	number: number;
	total: number;
	tried: OptionFeedback[];
	identity: Identity;
	onAnswered: (questionId: string, feedback: OptionFeedback) => void;
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
			setError(
				errorMessage(caught, "Your answer could not be sent. Try again."),
			);
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
					return (
						<li key={option.id} className={`quiz-option is-${state}`}>
							<button
								type="button"
								className="quiz-choice"
								disabled={Boolean(feedback) || solved || pending !== null}
								aria-busy={pending === option.id}
								onClick={() => pick(option.id)}
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
							{feedback &&
							(feedback.explanation || feedback.citations.length) ? (
								<Feedback feedback={feedback} />
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
						tried={progress.get(question.id)?.tried ?? []}
						identity={identity}
						onAnswered={(questionId, feedback) =>
							onAnswered(quiz.lecture_id, questionId, feedback)
						}
					/>
				))}
			</ol>
		</div>
	);
}
