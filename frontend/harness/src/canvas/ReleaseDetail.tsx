import { Download, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { responseError } from "../api";
import type {
	CoursePlan,
	CourseRelease,
	ReleaseValidationFinding,
} from "../models";
import { downloadResponse, errorMessage, Notice } from "../ui";

export function formatDate(value: string): string {
	const date = new Date(value);
	return Number.isNaN(date.getTime())
		? value
		: new Intl.DateTimeFormat(undefined, {
				dateStyle: "medium",
				timeStyle: "short",
			}).format(date);
}

export function findingScope(
	finding: ReleaseValidationFinding,
	course: CoursePlan | null,
): string {
	const target = finding.target;
	const parts: string[] = [];
	if (target.lecture_id) {
		const index =
			course?.lectures.findIndex(
				(lecture) => lecture.id === target.lecture_id,
			) ?? -1;
		parts.push(
			index >= 0
				? `Lecture ${index + 1} · ${course?.lectures[index].title}`
				: `Lecture ${target.lecture_id}`,
		);
	}
	if (target.slide_id) parts.push(`Slide ${target.slide_id}`);
	if (target.content_block)
		parts.push(target.content_block.replaceAll("_", " "));
	if (target.citation_index !== null)
		parts.push(`Citation ${target.citation_index + 1}`);
	if (parts.length === 0) parts.push(target.scope.replaceAll("_", " "));
	return parts.join(" · ");
}

export function ReleaseDetail({
	release,
	course,
}: {
	release: CourseRelease;
	course: CoursePlan | null;
}) {
	const [downloading, setDownloading] = useState<string | null>(null);
	const [status, setStatus] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const waiverByFinding = new Map(
		release.validation.waivers.map((waiver) => [
			waiver.finding_id,
			waiver.justification,
		]),
	);
	const lectureTitle = (lectureId: string) =>
		course?.lectures.find((lecture) => lecture.id === lectureId)?.title ??
		lectureId;

	async function retrieve(artifactId: string, regenerate: boolean) {
		const key = `${artifactId}:${regenerate ? "regenerate" : "download"}`;
		setDownloading(key);
		setError(null);
		setStatus(null);
		try {
			const response = await fetch(
				`/api/releases/${encodeURIComponent(release.slug)}/artifacts/${encodeURIComponent(artifactId)}${regenerate ? "/regenerate" : ""}`,
				regenerate ? { method: "POST" } : undefined,
			);
			if (!response.ok) throw new Error(await responseError(response));
			await downloadResponse(
				response,
				`${release.slug}-${artifactId}${regenerate ? "-regenerated" : ""}.pptx`,
			);
			setStatus(
				regenerate
					? "Regenerated file matches the published checksum and was downloaded."
					: "Stored file downloaded.",
			);
		} catch (caught) {
			setError(
				errorMessage(caught, "The release file could not be retrieved."),
			);
		} finally {
			setDownloading(null);
		}
	}

	return (
		<section
			className="release-detail panel panel-pad"
			aria-labelledby={`release-detail-${release.slug}`}
		>
			<div className="release-detail-head">
				<ShieldCheck aria-hidden="true" />
				<div>
					<h3 id={`release-detail-${release.slug}`}>{release.name}</h3>
					<p className="meta">
						<span className="mono">{release.slug}</span> · Published{" "}
						{formatDate(release.published_at)}
					</p>
				</div>
			</div>

			<p className="release-coverage">
				<strong>{release.included_lecture_ids.length}</strong> lectures included
				{" · "}
				<strong>{release.planned_unpublished_lecture_ids.length}</strong> still
				planned
			</p>

			<div className="release-detail-block">
				<h4>Files</h4>
				{release.artifacts.length === 0 ? (
					<p className="meta">
						No Presentation files — this release records the plan only.
					</p>
				) : (
					<ul className="row-list">
						{release.artifacts.map((artifact) => (
							<li className="row" key={artifact.id}>
								<div className="row-main">
									<span className="row-title">
										{lectureTitle(artifact.lecture_id)}
									</span>
									<span className="row-meta">
										PowerPoint ·{" "}
										{Math.max(
											1,
											Math.round(artifact.size / 1024),
										).toLocaleString()}{" "}
										KB ·{" "}
										<span className="mono">
											SHA-256 {artifact.sha256.slice(0, 12)}…
										</span>
									</span>
								</div>
								<div className="row-actions">
									<button
										type="button"
										className="btn btn-small"
										disabled={downloading !== null}
										onClick={() => void retrieve(artifact.id, false)}
									>
										<Download aria-hidden="true" />
										Download
									</button>
									<button
										type="button"
										className="btn btn-quiet btn-small"
										disabled={downloading !== null}
										onClick={() => void retrieve(artifact.id, true)}
									>
										Regenerate and verify
									</button>
								</div>
							</li>
						))}
					</ul>
				)}
			</div>

			<div className="release-detail-block">
				<h4>Checks when published</h4>
				{release.validation.findings.length === 0 ? (
					<p className="meta">No issues were recorded.</p>
				) : (
					<ul className="release-record">
						{release.validation.findings.map((finding) => (
							<li className={`is-${finding.severity}`} key={finding.id}>
								<p className="meta">{findingScope(finding, course)}</p>
								<p>{finding.message}</p>
								{finding.waived ? (
									<p className="release-waiver-record">
										Published with this warning:{" "}
										{waiverByFinding.get(finding.id)}
									</p>
								) : null}
							</li>
						))}
					</ul>
				)}
			</div>

			<details className="release-provenance">
				<summary>Pinned inputs</summary>
				<dl>
					<div>
						<dt>Template</dt>
						<dd>
							{release.template_profile.definition.name} · v
							{release.template_profile.version}
						</dd>
					</div>
					<div>
						<dt>Template profile</dt>
						<dd className="mono">{release.template_profile.id}</dd>
					</div>
					<div>
						<dt>Profile SHA-256</dt>
						<dd className="mono">{release.template_profile.profile_sha256}</dd>
					</div>
					{release.template_profile.template_sha256 ? (
						<div>
							<dt>Template SHA-256</dt>
							<dd className="mono">
								{release.template_profile.template_sha256}
							</dd>
						</div>
					) : null}
					<div>
						<dt>Course Revision</dt>
						<dd className="mono">{release.revision_id}</dd>
					</div>
				</dl>
				<h5>Source versions</h5>
				{release.sources.length === 0 ? (
					<p className="meta">No Source versions were pinned.</p>
				) : (
					<ul className="release-sources">
						{release.sources.map((source) => (
							<li key={source.id}>
								<strong>{source.label}</strong>
								<span className="mono">
									{source.source_version_id.slice(0, 16)}
								</span>
							</li>
						))}
					</ul>
				)}
			</details>

			{status ? (
				<Notice tone="success" role="status">
					{status}
				</Notice>
			) : null}
			{error ? <Notice tone="error">{error}</Notice> : null}
		</section>
	);
}
