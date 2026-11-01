import { type FormEvent, useId, useState } from "react";

import {
	type Identity,
	identityProblem,
	NAME_MAX,
	STUDENT_ID_MAX,
} from "./identity";

/** The name and student ID fields, with a submit button labelled by the caller. */
export function IdentityForm({
	initial,
	submitLabel,
	onSubmit,
	onCancel,
}: {
	initial: Identity | null;
	submitLabel: string;
	onSubmit: (identity: Identity) => void;
	onCancel?: () => void;
}) {
	const id = useId();
	const [name, setName] = useState(initial?.name ?? "");
	const [studentId, setStudentId] = useState(initial?.studentId ?? "");
	const [problem, setProblem] = useState<string | null>(null);

	function submit(event: FormEvent) {
		event.preventDefault();
		const identity = { name, studentId };
		const found = identityProblem(identity);
		setProblem(found);
		if (!found) onSubmit(identity);
	}

	return (
		<form className="identity-form" onSubmit={submit} noValidate>
			<div className="field">
				<label className="field-label" htmlFor={`${id}-name`}>
					Your name
				</label>
				<input
					id={`${id}-name`}
					className="input"
					autoComplete="name"
					maxLength={NAME_MAX}
					value={name}
					onChange={(event) => setName(event.target.value)}
				/>
			</div>
			<div className="field">
				<label className="field-label" htmlFor={`${id}-student`}>
					Student ID
				</label>
				<input
					id={`${id}-student`}
					className="input"
					autoComplete="off"
					spellCheck={false}
					maxLength={STUDENT_ID_MAX}
					value={studentId}
					onChange={(event) => setStudentId(event.target.value)}
					aria-describedby={`${id}-student-help`}
				/>
				<p id={`${id}-student-help`} className="field-help">
					Use the same student ID on every device to keep your progress.
				</p>
			</div>
			{problem ? (
				<p className="field-error" role="alert">
					{problem}
				</p>
			) : null}
			<div className="form-actions">
				{onCancel ? (
					<button type="button" className="btn" onClick={onCancel}>
						Cancel
					</button>
				) : null}
				<button type="submit" className="btn btn-primary">
					{submitLabel}
				</button>
			</div>
		</form>
	);
}
