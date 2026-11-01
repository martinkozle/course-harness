import {
	CitationList,
	errorMessage,
	Notice,
	SlidePreview,
} from "@course-harness/ui";
import { ArrowLeft, ArrowRight, Download } from "lucide-react";
import { useEffect, useState } from "react";

import { api, type Lecture } from "./api";
import type { LearnerState } from "./CourseShell";
import { Quiz } from "./Quiz";
import { coursePath, followLink, lecturePath, QUIZ_ANCHOR } from "./router";

/** One Lecture: its Slides in order with notes and Citations, then its Quiz. */
export function LecturePage({
	lectureId,
	state,
}: {
	lectureId: string;
	state: LearnerState;
}) {
	const { course } = state;
	const [lecture, setLecture] = useState<Lecture | null>(null);
	const [error, setError] = useState<string | null>(null);
	const quiz = state.quizzes?.find((item) => item.lecture_id === lectureId);

	useEffect(() => {
		let active = true;
		api
			.lecture(course.slug, lectureId)
			.then((loaded) => {
				if (active) setLecture(loaded);
			})
			.catch((caught) => {
				if (active)
					setError(errorMessage(caught, "This Lecture could not be loaded."));
			});
		return () => {
			active = false;
		};
	}, [course.slug, lectureId]);

	useEffect(() => {
		if (lecture) document.title = `${lecture.title} · ${course.title}`;
	}, [lecture, course.title]);

	// Arriving from Exercises, go straight to the questions once they are on the page.
	const quizReady = Boolean(lecture && quiz);
	useEffect(() => {
		if (quizReady && location.hash === `#${QUIZ_ANCHOR}`)
			document.getElementById(QUIZ_ANCHOR)?.scrollIntoView();
	}, [quizReady]);

	if (error) return <Notice tone="error">{error}</Notice>;
	if (!lecture) return <p className="meta">Loading…</p>;

	return (
		<article className="lecture">
			<nav className="crumbs" aria-label="Course">
				<a href={coursePath(course.slug)} onClick={followLink}>
					<ArrowLeft aria-hidden="true" />
					All Lectures
				</a>
			</nav>
			<header className="lecture-header">
				<p className="eyebrow">
					Lecture {lecture.number} of {lecture.lecture_count}
					{lecture.group ? ` · ${lecture.group}` : ""}
				</p>
				<h2 className="display-title">{lecture.title}</h2>
				{lecture.download_url ? (
					<p>
						<a className="btn btn-small" href={lecture.download_url} download>
							<Download aria-hidden="true" />
							Download slides (PPTX)
						</a>
					</p>
				) : null}
			</header>

			{lecture.slides.length ? (
				<ol className="slide-sequence" aria-label="Slides">
					{lecture.slides.map(({ slide, slots, background_url }, index) => (
						<li key={slide.id} className="slide-item">
							<figure>
								<SlidePreview
									slide={slide}
									slots={slots}
									backgroundUrl={background_url}
									aspectRatio={lecture.aspect_ratio}
									fallback="content"
									headingLevel={3}
								/>
								<figcaption className="slide-caption">
									<span className="slide-number">
										{index + 1} / {lecture.slides.length}
									</span>
									{slide.citations.length ? (
										<CitationList citations={slide.citations} />
									) : null}
								</figcaption>
							</figure>
							{slide.speaker_notes?.trim() ? (
								<details className="speaker-notes">
									<summary>Speaker notes</summary>
									<p>{slide.speaker_notes}</p>
								</details>
							) : null}
						</li>
					))}
				</ol>
			) : (
				<p className="meta">This Lecture has no slides.</p>
			)}

			{lecture.question_count ? (
				<section
					id={QUIZ_ANCHOR}
					className="check"
					aria-labelledby={`${QUIZ_ANCHOR}-title`}
				>
					<h3 id={`${QUIZ_ANCHOR}-title`} className="check-title">
						Check your understanding
					</h3>
					<p className="meta">
						Pick an answer. If it is not the one, you will see why; try again
						until you find it.
					</p>
					{quiz ? (
						<Quiz
							slug={course.slug}
							quiz={quiz}
							progress={state.progress}
							identity={state.identity}
							onAnswered={state.onAnswered}
						/>
					) : (
						<p className="meta">Loading the questions…</p>
					)}
				</section>
			) : null}

			<nav className="lecture-pager" aria-label="Lectures">
				{lecture.previous ? (
					<a
						className="pager-link"
						href={lecturePath(course.slug, lecture.previous.id)}
						onClick={followLink}
					>
						<span className="pager-direction">
							<ArrowLeft aria-hidden="true" />
							Previous
						</span>
						<span className="pager-title">{lecture.previous.title}</span>
					</a>
				) : (
					<span />
				)}
				{lecture.next ? (
					<a
						className="pager-link is-next"
						href={lecturePath(course.slug, lecture.next.id)}
						onClick={followLink}
					>
						<span className="pager-direction">
							Next
							<ArrowRight aria-hidden="true" />
						</span>
						<span className="pager-title">{lecture.next.title}</span>
					</a>
				) : null}
			</nav>
		</article>
	);
}
