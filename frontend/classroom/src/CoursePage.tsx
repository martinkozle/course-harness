import { errorMessage, Notice } from "@course-harness/ui";
import { Download } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError, api, type CourseSummary } from "./api";

export function CoursePage({ slug }: { slug: string }) {
	const [course, setCourse] = useState<CourseSummary | null>(null);
	const [error, setError] = useState<{
		message: string;
		noAccess: boolean;
	} | null>(null);

	useEffect(() => {
		let active = true;
		api
			.course(slug)
			.then((summary) => {
				if (!active) return;
				setCourse(summary);
				document.title = `${summary.title} · Classroom`;
			})
			.catch((caught) => {
				if (!active) return;
				setError({
					message: errorMessage(caught, "This course could not be loaded."),
					noAccess: caught instanceof ApiError && caught.status === 401,
				});
			});
		return () => {
			active = false;
		};
	}, [slug]);

	if (error) {
		return (
			<Notice tone={error.noAccess ? "info" : "error"}>
				{error.noAccess
					? "To see this course, open the link you were given. If it no longer works, ask for a new one."
					: error.message}
			</Notice>
		);
	}
	if (!course) return <p className="meta">Loading…</p>;

	return (
		<article className="course">
			<header className="course-header">
				<h2 className="display-title">{course.title}</h2>
				<p className="meta">
					{course.release
						? `${course.release.name} · ${course.role === "instructor" ? "You are viewing as an Instructor." : "Welcome."}`
						: "This course has no Release yet. Check back later."}
				</p>
			</header>
			{course.lectures.length ? (
				<ol className="lecture-list">
					{course.lectures.map((lecture, index) => (
						<li key={lecture.id} className="classroom-card lecture-row">
							<div>
								<p className="section-label">Lecture {index + 1}</p>
								<h3>{lecture.title}</h3>
								<p className="meta">
									{lecture.slide_count === 1
										? "1 slide"
										: `${lecture.slide_count} slides`}
									{lecture.has_quiz ? " · Check your understanding" : ""}
								</p>
							</div>
							{lecture.download_url ? (
								<a className="btn" href={lecture.download_url} download>
									<Download aria-hidden="true" />
									PPTX
								</a>
							) : null}
						</li>
					))}
				</ol>
			) : null}
		</article>
	);
}
