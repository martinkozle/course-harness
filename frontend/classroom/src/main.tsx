import "@course-harness/ui/styles.css";
import "./classroom.css";
import "./learner.css";
import "./insights.css";

import { Notice } from "@course-harness/ui";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AdminPage } from "./AdminPage";
import { CourseShell } from "./CourseShell";
import { type Route, useRoute } from "./router";

function Page({ route }: { route: Route }) {
	switch (route.page) {
		case "admin":
			return <AdminPage />;
		case "link-not-valid":
			return (
				<Notice tone="warning">
					That link is not valid any more. It may have been replaced by a newer
					one; ask the person who shared it for the current link.
				</Notice>
			);
		case "home":
			return (
				<p className="meta">To open a course, use the link you were given.</p>
			);
		default:
			return <Notice tone="info">There is no page at this address.</Notice>;
	}
}

function Classroom() {
	const route = useRoute();
	if (route.page === "course") {
		const { page: _page, slug, ...view } = route;
		return <CourseShell key={slug} slug={slug} view={view} />;
	}
	return (
		<div className="classroom">
			<header className="classroom-bar">
				<h1>Course Harness Classroom</h1>
				{route.page === "admin" ? (
					<span className="meta">Administration</span>
				) : null}
			</header>
			<main className="classroom-main">
				<Page route={route} />
			</main>
		</div>
	);
}

const root = document.getElementById("root");

if (!root) {
	throw new Error(
		"Course Harness Classroom could not find its application root",
	);
}

createRoot(root).render(
	<StrictMode>
		<Classroom />
	</StrictMode>,
);
