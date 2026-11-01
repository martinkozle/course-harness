import type { QuestionMark, QuizTally } from "./progress";

const LABEL: Record<QuestionMark, string> = {
	open: "not answered yet",
	tried: "tried, not yet correct",
	correct: "answered correctly",
};

/** One small mark per Question of a Quiz, in Quiz order. */
export function Marks({ tally }: { tally: QuizTally }) {
	return (
		<span
			className="marks"
			role="img"
			aria-label={tally.marks
				.map((mark, index) => `Question ${index + 1}: ${LABEL[mark]}`)
				.join("; ")}
		>
			{tally.marks.map((mark, index) => (
				// biome-ignore lint/suspicious/noArrayIndexKey: marks follow Question order
				<span key={index} className={`mark is-${mark}`} />
			))}
		</span>
	);
}
