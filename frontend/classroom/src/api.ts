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
	/** The full link, or null for a link made before links were kept: rotate it to show it. */
	url: string | null;
	needs_rotation: boolean;
};

/** A course's Share Links as its Instructors see them, and whose links they may rotate. */
export type CourseLinks = {
	links: ShareLink[];
	can_rotate: Role[];
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

/** A Source the served Release pins: its label and, when it has one, its public link. */
export type CourseSource = {
	id: string;
	label: string;
	url: string | null;
	doi: string | null;
	arxiv_id: string | null;
};

export type CourseSummary = {
	slug: string;
	title: string;
	/** What the server lets you do here; viewing as a Learner never changes it. */
	role: Role;
	/** Whether you are the Administrator, who is an Instructor of every course. */
	administrator: boolean;
	release: ServedRelease | null;
	lectures: LectureSummary[];
	sources: CourseSource[];
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

/** Instructor Insights, graded against one Release (ADR 0018). */
export type InsightsRelease = {
	tag: string;
	name: string | null;
	published_at: string | null;
	/** What changed since the previous Release, as the Course Author wrote it. */
	notes?: string | null;
	answers: number;
	learners: number;
	first_answer_at: string | null;
	last_answer_at: string | null;
	served: boolean;
	graded: boolean;
	readable: boolean;
};

export type AnswerChanges = {
	answer_key_changed: number;
	question_changed: number;
	option_removed: number;
	release_unavailable: number;
};

export type QuestionInsight = {
	lecture_id: string;
	lecture_title: string;
	question_id: string;
	number: number;
	prompt: string;
	option_count: number;
	correct_option_text: string | null;
	attempts: number;
	learners: number;
	correct_learners: number;
	first_try_correct_rate: number | null;
	mean_score: number | null;
	most_chosen_wrong: {
		option_id: string;
		text: string;
		learners: number;
		picks: number;
	} | null;
	releases: { tag: string; answers: number }[];
	changes: AnswerChanges;
	gradable: boolean;
};

export type StudentInsight = {
	student_id: string;
	name: string;
	role: Role;
	attempts: number;
	questions_answered: number;
	correct: number;
	total_score: number;
	last_activity: string;
};

export type Insights = {
	graded_release: InsightsRelease;
	include_instructors: boolean;
	overview: {
		learners: number;
		questions: number;
		answers: number;
		/** Mean total score per Learner over all Questions, unanswered ones counting 0. */
		average_score: number | null;
		average_total_score: number | null;
		/** Mean score over answered Questions only. */
		mean_answered_score: number | null;
		hidden_instructor_answers: number;
	};
	questions: QuestionInsight[];
	removed_questions: {
		question_id: string;
		lecture_id: string;
		prompt: string | null;
		answers: number;
		learners: number;
	}[];
	students: StudentInsight[];
	releases: InsightsRelease[];
};

export type CourseSync = {
	syncing: boolean;
	last_sync_at: string | null;
	sync_status: SyncStatus;
	sync_message: string | null;
	release: ServedRelease | null;
};

export type InsightsOptions = {
	release: string | null;
	includeInstructors: boolean;
};

function insightsQuery({ release, includeInstructors }: InsightsOptions) {
	const query = new URLSearchParams();
	if (release) query.set("release", release);
	if (includeInstructors) query.set("include_instructors", "true");
	const text = query.toString();
	return text ? `?${text}` : "";
}

/** Where the CSV of every Answer, graded like the Insights shown, is downloaded from. */
export const answersCsvUrl = (slug: string, options: InsightsOptions) =>
	`/api/c/${encodeURIComponent(slug)}/answers.csv${insightsQuery(options)}`;

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
	insights: (slug: string, options: InsightsOptions) =>
		request<Insights>(
			"GET",
			`/api/c/${encodeURIComponent(slug)}/insights${insightsQuery(options)}`,
		),
	syncStatus: (slug: string) =>
		request<CourseSync>("GET", `/api/c/${encodeURIComponent(slug)}/sync`),
	syncNow: (slug: string) =>
		request<CourseSync>("POST", `/api/c/${encodeURIComponent(slug)}/sync`),
	courseLinks: (slug: string) =>
		request<CourseLinks>("GET", `/api/c/${encodeURIComponent(slug)}/links`),
	rotateCourseLink: (slug: string, role: Role) =>
		request<ShareLink>(
			"POST",
			`/api/c/${encodeURIComponent(slug)}/links/${role}`,
		),
};
