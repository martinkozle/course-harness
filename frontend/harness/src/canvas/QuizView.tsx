import {
	CitationList,
	errorMessage,
	isAbort,
	Notice,
} from "@course-harness/ui";
import { Check, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";

import { responseError } from "../api";
import { Markdown } from "../chat/Markdown";
import type {
	Lecture,
	Quiz,
	QuizOption,
	QuizQuestion,
	ReaderTarget,
	Source,
} from "../models";
import type { AgentContext } from "../useCourseAgent";
import { evidenceOpening } from "./evidenceOpening";

const LETTERS = "ABCDEFGHIJ";

/** A read-only view of a Lecture's Quiz: every Question, its answer key and Explanations. */
export function QuizView({
	lecture,
	lectureNumber,
	context,
	quizVersion,
	sources,
	busy,
	onAskAgent,
	onOpenEvidence,
}: {
	lecture: Lecture;
	lectureNumber: number;
	/** The focus to give the agent for a request about the whole Quiz. */
	context: AgentContext;
	/** Changes whenever a Quiz may have changed, so the view reloads. */
	quizVersion: number;
	sources: Source[];
	busy: boolean;
	onAskAgent: (request: string, context?: AgentContext | null) => void;
	onOpenEvidence: (target: ReaderTarget) => void;
}) {
	const [quiz, setQuiz] = useState<Quiz | null | undefined>(undefined);
	const [error, setError] = useState<string | null>(null);

	// biome-ignore lint/correctness/useExhaustiveDependencies: quizVersion asks for a reload
	useEffect(() => {
		const controller = new AbortController();
		void (async () => {
			try {
				const response = await fetch(
					`/api/quizzes/${encodeURIComponent(lecture.id)}`,
					{ signal: controller.signal },
				);
				if (response.status === 404) {
					setQuiz(null);
					setError(null);
					return;
				}
				if (!response.ok) throw new Error(await responseError(response));
				setQuiz((await response.json()) as Quiz);
				setError(null);
			} catch (caught) {
				if (isAbort(caught)) return;
				setQuiz(null);
				setError(errorMessage(caught, "The Quiz could not be loaded."));
			}
		})();
		return () => controller.abort();
	}, [lecture.id, quizVersion]);

	const quizContext = context;
	const opening = evidenceOpening(sources, onOpenEvidence);

	if (quiz === undefined)
		return (
			<p className="meta" role="status">
				Loading the Quiz…
			</p>
		);
	if (error) return <Notice tone="error">{error}</Notice>;
	if (quiz === null)
		return (
			<div className="canvas-empty lecture-empty">
				<h2>No Quiz yet</h2>
				<p>
					A Quiz checks what Learners took from this Lecture. The agent can
					write one from your Sources, with an Explanation for every wrong
					answer.
				</p>
				<button
					type="button"
					className="btn btn-primary"
					disabled={busy}
					onClick={() =>
						onAskAgent(
							`Write a Quiz for “${lecture.title}” grounded in the course Sources.`,
							quizContext,
						)
					}
				>
					<Sparkles aria-hidden="true" />
					Write a Quiz with the agent
				</button>
			</div>
		);

	return (
		<section className="quiz-view" aria-label="Quiz">
			<div className="quiz-bar">
				<p className="meta">
					{quiz.questions.length} Question
					{quiz.questions.length === 1 ? "" : "s"} · Learners see the Options in
					a shuffled order
				</p>
				<button
					type="button"
					className="btn btn-quiet"
					aria-label="Ask the agent about this Quiz"
					onClick={() => onAskAgent("Improve this Quiz: ", quizContext)}
				>
					<Sparkles aria-hidden="true" />
					Ask the agent
				</button>
			</div>
			<ol className="quiz-questions">
				{quiz.questions.map((question, index) => (
					<QuestionCard
						key={question.id}
						question={question}
						number={index + 1}
						opening={opening}
						onAsk={() =>
							onAskAgent("Improve this Question: ", {
								key: `question-${question.id}`,
								label: `Question ${index + 1} · Lecture ${lectureNumber}`,
								instruction: `I'm reviewing Question ${index + 1} (${question.id}) of the Quiz for the Lecture "${lecture.title}"`,
							})
						}
					/>
				))}
			</ol>
		</section>
	);
}

function QuestionCard({
	question,
	number,
	opening,
	onAsk,
}: {
	question: QuizQuestion;
	number: number;
	opening: ReturnType<typeof evidenceOpening>;
	onAsk: () => void;
}) {
	const headingId = `quiz-question-${question.id}`;
	return (
		<li className="quiz-question">
			<div className="quiz-question-head">
				<h2 id={headingId} className="quiz-question-number">
					Question {number}
				</h2>
				<button
					type="button"
					className="btn btn-quiet btn-small"
					aria-label={`Ask the agent about Question ${number}`}
					onClick={onAsk}
				>
					<Sparkles aria-hidden="true" />
					Ask the agent
				</button>
			</div>
			<div className="quiz-prompt">
				<Markdown text={question.prompt} />
			</div>
			<ol
				className="quiz-options"
				aria-label={`Options for Question ${number}`}
			>
				{question.options.map((option, index) => (
					<OptionRow
						key={option.id}
						option={option}
						letter={LETTERS[index] ?? String(index + 1)}
						opening={opening}
					/>
				))}
			</ol>
			{question.citations.length > 0 ? (
				<div className="quiz-sources">
					<h3 className="section-label">
						Citations ({question.citations.length})
					</h3>
					<CitationList citations={question.citations} opening={opening} />
				</div>
			) : null}
		</li>
	);
}

function OptionRow({
	option,
	letter,
	opening,
}: {
	option: QuizOption;
	letter: string;
	opening: ReturnType<typeof evidenceOpening>;
}) {
	return (
		<li className={option.correct ? "quiz-option is-correct" : "quiz-option"}>
			<div className="quiz-option-head">
				<span className="quiz-letter" aria-hidden="true">
					{letter}
				</span>
				<span className="quiz-option-text">{option.text}</span>
				<span className="quiz-verdict">
					{option.correct ? (
						<>
							<Check aria-hidden="true" />
							Correct
						</>
					) : (
						<>
							<X aria-hidden="true" />
							Wrong
						</>
					)}
				</span>
			</div>
			{option.explanation ? (
				<div className="quiz-explanation">
					<Markdown text={option.explanation} />
				</div>
			) : !option.correct ? (
				<p className="quiz-explanation is-missing">
					No Explanation yet. Learners who pick this Option will not see why it
					is wrong.
				</p>
			) : null}
			{option.citations.length > 0 ? (
				<CitationList citations={option.citations} opening={opening} />
			) : null}
		</li>
	);
}
