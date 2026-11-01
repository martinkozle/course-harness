import { useEffect, useState } from "react";

export type Route =
	| { page: "home" }
	| { page: "admin" }
	| { page: "course"; slug: string }
	| { page: "link-not-valid" }
	| { page: "not-found" };

export function parseRoute(pathname: string): Route {
	const path = pathname.replace(/\/+$/, "") || "/";
	if (path === "/") return { page: "home" };
	if (path === "/admin") return { page: "admin" };
	if (path === "/link-not-valid") return { page: "link-not-valid" };
	const course = /^\/c\/([a-z0-9-]{1,64})$/.exec(path);
	if (course) return { page: "course", slug: course[1] };
	return { page: "not-found" };
}

export function useRoute(): Route {
	const [route, setRoute] = useState(() => parseRoute(location.pathname));
	useEffect(() => {
		const update = () => setRoute(parseRoute(location.pathname));
		window.addEventListener("popstate", update);
		return () => window.removeEventListener("popstate", update);
	}, []);
	return route;
}
