import { useCallback, useState } from "react";

/**
 * The name and student ID a Learner gives. It is trust-based and not a security boundary (the
 * course cookie is); it goes with every Answer so that progress follows the student ID.
 */
export type Identity = { name: string; studentId: string };

const KEY = "classroom.identity";
export const NAME_MAX = 100;
export const STUDENT_ID_MAX = 64;

// Printable text, as the server accepts.
// biome-ignore lint/suspicious/noControlCharactersInRegex: refusing control characters is the point
const CONTROL = /[\u0000-\u001f\u007f]/;

export function identityProblem(identity: Identity): string | null {
	if (!identity.name.trim()) return "Enter your name.";
	if (!identity.studentId.trim()) return "Enter your student ID.";
	if (identity.name.trim().length > NAME_MAX)
		return `Keep your name under ${NAME_MAX} characters.`;
	if (identity.studentId.trim().length > STUDENT_ID_MAX)
		return `Keep your student ID under ${STUDENT_ID_MAX} characters.`;
	if (CONTROL.test(identity.name) || CONTROL.test(identity.studentId))
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
