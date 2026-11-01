import type { PreviewSlot, Slide, SlideCitation } from "@course-harness/ui";

// The Classroom HTTP API. Every request sends the header the server requires on state-changing
// requests, so a cross-site form or image can never make one.

export type Role = "learner" | "instructor";
export type SyncStatus = "pending" | "ok" | "warning" | "error";

export type ServedRelease = {
	tag: string;
	name: string;
	published_at: string;
};

export type ShareLink = {
	role: Role;
	created_at: string;
	rotated_at: string | null;
};

export type AdminCourse = {
	slug: string;
	repo_url: string;
	title: string | null;
	release: ServedRelease | null;
	last_sync_at: string | null;
	sync_status: SyncStatus;
	sync_message: string | null;
	links: ShareLink[];
};

export type CreatedShareLink = {
	role: Role;
	url: string;
	link: ShareLink;
};

export type AdminSession = {
	configured: boolean;
	authenticated: boolean;
};

export type LectureSummary = {
	id: string;
	title: string;
	group: string | null;
	slide_count: number;
	has_quiz: boolean;
	question_count: number;
	download_url: string | null;
};

export type CourseSummary = {
	slug: string;
	title: string;
	role: Role;
	release: ServedRelease | null;
	lectures: LectureSummary[];
};

export type LectureLink = { id: string; title: string };

export type LearnerSlide = {
	slide: Slide;
	slots: Record<string, PreviewSlot> | null;
	background_url: string | null;
};

export type Lecture = {
	id: string;
	title: string;
	group: string | null;
	number: number;
	lecture_count: number;
	previous: LectureLink | null;
	next: LectureLink | null;
	aspect_ratio: number;
	slides: LearnerSlide[];
	download_url: string | null;
	question_count: number;
};

/** A Question as a Learner sees it before answering: no correctness, no Explanations. */
export type LearnerQuestion = {
	id: string;
	prompt: string;
	options: { id: string; text: string }[];
};

export type LearnerQuiz = {
	lecture_id: string;
	lecture_title: string;
	questions: LearnerQuestion[];
};

/** What a Learner learns about an Option they picked. */
export type OptionFeedback = {
	option_id: string;
	correct: boolean;
	explanation: string | null;
	citations: SlideCitation[];
};

export type AnswerResult = OptionFeedback & { recorded: boolean };

export type QuestionProgress = {
	lecture_id: string;
	question_id: string;
	correct: boolean;
	tried: OptionFeedback[];
};

export type Progress = {
	student_id: string;
	questions: QuestionProgress[];
};

export type AnswerRequest = {
	lecture_id: string;
	question_id: string;
	option_id: string;
	learner_name: string;
	student_id: string;
};

export class ApiError extends Error {
	constructor(
		message: string,
		readonly status: number,
	) {
		super(message);
	}
}

/** The student ID travels in a header, never in a URL, so it stays out of access logs. */
function studentHeader(studentId: string): Record<string, string> {
	return { "X-Classroom-Student": encodeURIComponent(studentId) };
}

async function request<T>(
	method: string,
	path: string,
	body?: unknown,
	headers: Record<string, string> = {},
): Promise<T> {
	const response = await fetch(path, {
		method,
		credentials: "same-origin",
		headers: {
			"X-Classroom-Request": "1",
			...(body === undefined ? {} : { "Content-Type": "application/json" }),
			...headers,
		},
		body: body === undefined ? undefined : JSON.stringify(body),
	});
	if (!response.ok) {
		let detail = `The Classroom answered ${response.status}.`;
		try {
			const payload = (await response.json()) as { detail?: unknown };
			if (typeof payload.detail === "string") detail = payload.detail;
		} catch {
			// Keep the generic message.
		}
		throw new ApiError(detail, response.status);
	}
	if (response.status === 204) return undefined as T;
	return (await response.json()) as T;
}

export const api = {
	adminSession: () => request<AdminSession>("GET", "/api/admin/session"),
	signIn: (token: string) =>
		request<AdminSession>("POST", "/api/admin/session", { token }),
	signOut: () => request<void>("DELETE", "/api/admin/session"),
	courses: () => request<AdminCourse[]>("GET", "/api/admin/courses"),
	attach: (repoUrl: string, slug: string | null) =>
		request<AdminCourse>("POST", "/api/admin/courses", {
			repo_url: repoUrl,
			slug,
		}),
	detach: (slug: string) =>
		request<void>("DELETE", `/api/admin/courses/${encodeURIComponent(slug)}`),
	sync: (slug: string) =>
		request<AdminCourse>(
			"POST",
			`/api/admin/courses/${encodeURIComponent(slug)}/sync`,
		),
	createLink: (slug: string, role: Role) =>
		request<CreatedShareLink>(
			"POST",
			`/api/admin/courses/${encodeURIComponent(slug)}/links/${role}`,
		),
	course: (slug: string) =>
		request<CourseSummary>("GET", `/api/c/${encodeURIComponent(slug)}`),
	lecture: (slug: string, lectureId: string) =>
		request<Lecture>(
			"GET",
			`/api/c/${encodeURIComponent(slug)}/lectures/${encodeURIComponent(lectureId)}`,
		),
	quizzes: (slug: string, studentId: string) =>
		request<LearnerQuiz[]>(
			"GET",
			`/api/c/${encodeURIComponent(slug)}/quizzes`,
			undefined,
			studentHeader(studentId),
		),
	progress: (slug: string, studentId: string) =>
		request<Progress>(
			"GET",
			`/api/c/${encodeURIComponent(slug)}/progress`,
			undefined,
			studentHeader(studentId),
		),
	answer: (slug: string, answer: AnswerRequest) =>
		request<AnswerResult>(
			"POST",
			`/api/c/${encodeURIComponent(slug)}/answers`,
			answer,
		),
};
