import { useCallback, useState } from "react";

/**
 * The name and student ID a Learner gives. It is trust-based and not a security boundary (the
 * course cookie is); it goes with every Answer so that progress follows the student ID.
 */
export type Identity = { name: string; studentId: string };

const KEY = "classroom.identity";
export const NAME_MAX = 100;
export const STUDENT_ID_MAX = 64;

// Plain text, as the server accepts: no C0 or C1 controls, line or paragraph separators, or
// bidirectional overrides and isolates.
const UNPRINTABLE =
	// biome-ignore lint/suspicious/noControlCharactersInRegex: refusing control characters is the point
	/[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069]/;

export function identityProblem(identity: Identity): string | null {
	if (!identity.name.trim()) return "Enter your name.";
	if (!identity.studentId.trim()) return "Enter your student ID.";
	if (identity.name.trim().length > NAME_MAX)
		return `Keep your name under ${NAME_MAX} characters.`;
	if (identity.studentId.trim().length > STUDENT_ID_MAX)
		return `Keep your student ID under ${STUDENT_ID_MAX} characters.`;
	if (UNPRINTABLE.test(identity.name) || UNPRINTABLE.test(identity.studentId))
		return "Use letters, digits and punctuation only.";
	return null;
}

function stored(): Identity | null {
	try {
		const raw = localStorage.getItem(KEY);
		if (!raw) return null;
		const value = JSON.parse(raw) as Partial<Identity>;
		const identity = {
			name: typeof value.name === "string" ? value.name : "",
			studentId: typeof value.studentId === "string" ? value.studentId : "",
		};
		return identityProblem(identity) ? null : identity;
	} catch {
		return null;
	}
}

/** The identity kept in this browser, shared by every course it opens. */
export function useIdentity(): [Identity | null, (identity: Identity) => void] {
	const [identity, setIdentity] = useState(stored);
	const save = useCallback((next: Identity) => {
		const clean = { name: next.name.trim(), studentId: next.studentId.trim() };
		try {
			localStorage.setItem(KEY, JSON.stringify(clean));
		} catch {
			// Without storage the identity lasts until the page is closed.
		}
		setIdentity(clean);
	}, []);
	return [identity, save];
}

const INSTRUCTOR_KEY = "classroom.instructor-id";

/**
 * The identity an Instructor's own tries go under. Instructors have no student ID, so each browser
 * gets its own: their tries are recorded as an Instructor's and stay out of Insights by default.
 */
export function instructorIdentity(): Identity {
	let id: string | null = null;
	try {
		id = localStorage.getItem(INSTRUCTOR_KEY);
		if (!id) {
			id = `instructor-${crypto.randomUUID().slice(0, 8)}`;
			localStorage.setItem(INSTRUCTOR_KEY, id);
		}
	} catch {
		// Without storage, the tries last until the page is closed.
		id ??= `instructor-${crypto.randomUUID().slice(0, 8)}`;
	}
	return { name: "Instructor", studentId: id };
}
