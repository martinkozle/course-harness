import type {
	LearnerQuiz,
	OptionFeedback,
	Progress,
	QuestionProgress,
} from "./api";

/** A student's state on each Question they answered, by `questionKey`. */
export type ProgressMap = Map<string, QuestionProgress>;

/** A Question is identified by its Lecture and its own ID, as the Classroom records it. */
export function questionKey(lectureId: string, questionId: string): string {
	return `${lectureId}/${questionId}`;
}

export type QuestionMark = "open" | "tried" | "correct";

export type QuizTally = {
	total: number;
	answered: number;
	correct: number;
	marks: QuestionMark[];
};

export function progressMap(progress: Progress): ProgressMap {
	return new Map(
		progress.questions.map((item) => [
			questionKey(item.lecture_id, item.question_id),
			item,
		]),
	);
}

/** Add one answered Option to a Question's progress. */
export function withAnswer(
	map: ProgressMap,
	lectureId: string,
	questionId: string,
	feedback: OptionFeedback,
): ProgressMap {
	const key = questionKey(lectureId, questionId);
	const current = map.get(key);
	const tried = current?.tried ?? [];
	const next = new Map(map);
	next.set(key, {
		lecture_id: lectureId,
		question_id: questionId,
		correct: Boolean(current?.correct) || feedback.correct,
		tried: tried.some((item) => item.option_id === feedback.option_id)
			? tried
			: [...tried, feedback],
	});
	return next;
}

export function markOf(item: QuestionProgress | undefined): QuestionMark {
	if (!item) return "open";
	return item.correct ? "correct" : "tried";
}

export function tally(quiz: LearnerQuiz, map: ProgressMap): QuizTally {
	const marks = quiz.questions.map((question) =>
		markOf(map.get(questionKey(quiz.lecture_id, question.id))),
	);
	return {
		total: marks.length,
		answered: marks.filter((mark) => mark !== "open").length,
		correct: marks.filter((mark) => mark === "correct").length,
		marks,
	};
}

export function tallySummary({ total, answered, correct }: QuizTally): string {
	if (total === 0) return "No questions";
	if (correct === total)
		return total === 1
			? "Answered correctly"
			: `All ${total} answered correctly`;
	if (answered === 0)
		return total === 1
			? "1 question to answer"
			: `${total} questions to answer`;
	return `${correct} of ${total} answered correctly`;
}
