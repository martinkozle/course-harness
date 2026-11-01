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
	download_url: string | null;
};

export type CourseSummary = {
	slug: string;
	title: string;
	role: Role;
	release: ServedRelease | null;
	lectures: LectureSummary[];
};

export class ApiError extends Error {
	constructor(
		message: string,
		readonly status: number,
	) {
		super(message);
	}
}

async function request<T>(
	method: string,
	path: string,
	body?: unknown,
): Promise<T> {
	const response = await fetch(path, {
		method,
		credentials: "same-origin",
		headers: {
			"X-Classroom-Request": "1",
			...(body === undefined ? {} : { "Content-Type": "application/json" }),
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
};
