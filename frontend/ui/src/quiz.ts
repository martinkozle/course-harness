import type { SlideCitation } from "./slides";

/** One answer choice of a Question. `text` is plain; `explanation` is Markdown. */
export type QuizOption = {
	id: string;
	text: string;
	correct: boolean;
	explanation: string | null;
	citations: SlideCitation[];
};

/** One single-answer multiple-choice item. `prompt` is Markdown. */
export type QuizQuestion = {
	id: string;
	prompt: string;
	options: QuizOption[];
	citations: SlideCitation[];
};

/** The Quiz that checks understanding of one Lecture. */
export type Quiz = {
	schema_version: 1;
	lecture_id: string;
	questions: QuizQuestion[];
};
