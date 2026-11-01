import { type MouseEvent, useEffect, useState } from "react";

export type CourseView =
	| { view: "lectures" }
	| { view: "exercises" }
	| { view: "insights" }
	| { view: "lecture"; lectureId: string };

export type Route =
	| { page: "home" }
	| { page: "admin" }
	| ({ page: "course"; slug: string } & CourseView)
	| { page: "link-not-valid" }
	| { page: "not-found" };

export function parseRoute(pathname: string): Route {
	const path = pathname.replace(/\/+$/, "") || "/";
	if (path === "/") return { page: "home" };
	if (path === "/admin") return { page: "admin" };
	if (path === "/link-not-valid") return { page: "link-not-valid" };
	const course =
		/^\/c\/([a-z0-9-]{1,64})(?:\/(exercises|insights)|\/lectures\/(lecture-[0-9a-f]{12}))?$/.exec(
			path,
		);
	if (course) {
		const [, slug, tab, lectureId] = course;
		if (lectureId) return { page: "course", slug, view: "lecture", lectureId };
		if (tab === "insights") return { page: "course", slug, view: "insights" };
		return { page: "course", slug, view: tab ? "exercises" : "lectures" };
	}
	return { page: "not-found" };
}

export const coursePath = (slug: string) => `/c/${slug}`;
export const exercisesPath = (slug: string) => `/c/${slug}/exercises`;
export const insightsPath = (slug: string) => `/c/${slug}/insights`;
export const lecturePath = (slug: string, lectureId: string) =>
	`/c/${slug}/lectures/${lectureId}`;
export const QUIZ_ANCHOR = "check-your-understanding";

const NAVIGATED = "classroom:navigate";

/** Show another page of the Classroom without reloading it. */
export function navigate(path: string) {
	if (path === location.pathname + location.hash) return;
	history.pushState(null, "", path);
	window.dispatchEvent(new Event(NAVIGATED));
	if (!path.includes("#")) window.scrollTo({ top: 0 });
}

/** An anchor's click handler that navigates in place, leaving modified clicks to the browser. */
export function followLink(event: MouseEvent<HTMLAnchorElement>) {
	if (
		event.defaultPrevented ||
		event.button !== 0 ||
		event.metaKey ||
		event.ctrlKey ||
		event.shiftKey ||
		event.altKey
	)
		return;
	event.preventDefault();
	const url = new URL(event.currentTarget.href);
	navigate(url.pathname + url.hash);
}

export function useRoute(): Route {
	const [route, setRoute] = useState(() => parseRoute(location.pathname));
	useEffect(() => {
		const update = () => setRoute(parseRoute(location.pathname));
		window.addEventListener("popstate", update);
		window.addEventListener(NAVIGATED, update);
		return () => {
			window.removeEventListener("popstate", update);
			window.removeEventListener(NAVIGATED, update);
		};
	}, []);
	return route;
}
