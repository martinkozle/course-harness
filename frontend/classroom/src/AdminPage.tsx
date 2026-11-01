import { ConfirmDialog, errorMessage, Notice } from "@course-harness/ui";
import { Copy, Link2, LogOut, RefreshCw, Trash2 } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useId, useState } from "react";

import {
	type AdminCourse,
	type AdminSession,
	ApiError,
	api,
	type CreatedShareLink,
	type Role,
} from "./api";

const ROLE_LABEL: Record<Role, string> = {
	learner: "Learner",
	instructor: "Instructor",
};

function formatTime(value: string | null): string {
	if (!value) return "never";
	const date = new Date(value);
	return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function AdminPage() {
	const [session, setSession] = useState<AdminSession | null>(null);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		api
			.adminSession()
			.then(setSession)
			.catch((caught) =>
				setError(errorMessage(caught, "The Classroom could not be reached.")),
			);
	}, []);

	if (error) return <Notice tone="error">{error}</Notice>;
	if (!session) return <p className="meta">Loading…</p>;
	if (!session.configured) {
		return (
			<Notice tone="warning">
				This Classroom has no Administrator token yet. Set{" "}
				<code>CLASSROOM_ADMIN_TOKEN</code> in the server environment and restart
				it.
			</Notice>
		);
	}
	if (!session.authenticated) {
		return <SignIn onSignedIn={setSession} />;
	}
	return (
		<Courses
			onSignedOut={() => setSession({ configured: true, authenticated: false })}
		/>
	);
}

function SignIn({
	onSignedIn,
}: {
	onSignedIn: (session: AdminSession) => void;
}) {
	const [token, setToken] = useState("");
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const inputId = useId();

	async function submit(event: FormEvent) {
		event.preventDefault();
		setBusy(true);
		setError(null);
		try {
			onSignedIn(await api.signIn(token));
		} catch (caught) {
			setError(errorMessage(caught, "You could not be signed in."));
		} finally {
			setBusy(false);
		}
	}

	return (
		<form className="classroom-card sign-in" onSubmit={submit}>
			<h2>Sign in as the Administrator</h2>
			<div className="field">
				<label htmlFor={inputId}>Administrator token</label>
				<input
					id={inputId}
					type="password"
					autoComplete="current-password"
					value={token}
					onChange={(event) => setToken(event.target.value)}
					aria-invalid={error ? true : undefined}
					required
				/>
				<small>
					Paste the value of <code>CLASSROOM_ADMIN_TOKEN</code> from the server
					environment.
				</small>
			</div>
			{error ? <Notice tone="error">{error}</Notice> : null}
			<div className="form-actions">
				<button
					type="submit"
					className="btn btn-primary"
					disabled={busy || !token}
				>
					Sign in
				</button>
			</div>
		</form>
	);
}

function Courses({ onSignedOut }: { onSignedOut: () => void }) {
	const [courses, setCourses] = useState<AdminCourse[] | null>(null);
	const [error, setError] = useState<string | null>(null);

	const handleError = useCallback(
		(caught: unknown, fallback: string) => {
			if (caught instanceof ApiError && caught.status === 401) {
				onSignedOut();
				return;
			}
			setError(errorMessage(caught, fallback));
		},
		[onSignedOut],
	);

	useEffect(() => {
		api
			.courses()
			.then(setCourses)
			.catch((caught) => handleError(caught, "Courses could not be loaded."));
	}, [handleError]);

	function replace(course: AdminCourse) {
		setCourses((current) =>
			current?.some((item) => item.slug === course.slug)
				? current.map((item) => (item.slug === course.slug ? course : item))
				: [...(current ?? []), course],
		);
	}

	async function signOut() {
		try {
			await api.signOut();
		} finally {
			onSignedOut();
		}
	}

	return (
		<div className="admin">
			<div className="admin-header">
				<h2 className="display-title">Courses</h2>
				<button type="button" className="btn btn-quiet" onClick={signOut}>
					<LogOut aria-hidden="true" />
					Sign out
				</button>
			</div>
			{error ? (
				<Notice
					tone="error"
					actions={
						<button
							type="button"
							className="btn btn-small"
							onClick={() => setError(null)}
						>
							Dismiss
						</button>
					}
				>
					{error}
				</Notice>
			) : null}
			<AttachForm onAttached={replace} onError={handleError} />
			{courses === null ? (
				<p className="meta">Loading…</p>
			) : courses.length === 0 ? (
				<p className="empty">
					No courses yet. Attach the public HTTPS repository a Course Author
					pushed their Course to.
				</p>
			) : (
				<ul className="course-list">
					{courses.map((course) => (
						<CourseCard
							key={course.slug}
							course={course}
							onChanged={replace}
							onDetached={() =>
								setCourses(
									(current) =>
										current?.filter((item) => item.slug !== course.slug) ??
										null,
								)
							}
							onError={handleError}
						/>
					))}
				</ul>
			)}
		</div>
	);
}

function AttachForm({
	onAttached,
	onError,
}: {
	onAttached: (course: AdminCourse) => void;
	onError: (caught: unknown, fallback: string) => void;
}) {
	const [repoUrl, setRepoUrl] = useState("");
	const [slug, setSlug] = useState("");
	const [busy, setBusy] = useState(false);
	const urlId = useId();
	const slugId = useId();

	async function submit(event: FormEvent) {
		event.preventDefault();
		setBusy(true);
		try {
			onAttached(await api.attach(repoUrl.trim(), slug.trim() || null));
			setRepoUrl("");
			setSlug("");
		} catch (caught) {
			onError(caught, "The repository could not be attached.");
		} finally {
			setBusy(false);
		}
	}

	return (
		<form className="classroom-card attach-form" onSubmit={submit}>
			<h3>Attach a course</h3>
			<div className="attach-fields">
				<div className="field">
					<label htmlFor={urlId}>Repository address</label>
					<input
						id={urlId}
						type="text"
						inputMode="url"
						autoComplete="off"
						spellCheck={false}
						placeholder="https://github.com/owner/course"
						value={repoUrl}
						onChange={(event) => setRepoUrl(event.target.value)}
						required
					/>
					<small>
						A public HTTPS Git repository, without credentials. The address works with or
						without .git at the end. The course appears once a Release has been published to
						it.
					</small>
				</div>
				<div className="field">
					<label htmlFor={slugId}>Course address (optional)</label>
					<input
						id={slugId}
						pattern="[a-z0-9]([a-z0-9\-]{0,62}[a-z0-9])?"
						placeholder="causal-inference"
						value={slug}
						onChange={(event) => setSlug(event.target.value)}
					/>
					<small>Lowercase letters, digits and hyphens; used in /c/…</small>
				</div>
			</div>
			<div className="form-actions">
				<button
					type="submit"
					className="btn btn-primary"
					disabled={busy || !repoUrl.trim()}
				>
					{busy ? "Attaching and syncing…" : "Attach"}
				</button>
			</div>
		</form>
	);
}

function StatusBadge({ course }: { course: AdminCourse }) {
	const label = {
		pending: "Not synced yet",
		ok: "Up to date",
		warning: "Synced with warnings",
		error: "Sync failed",
	}[course.sync_status];
	const tone =
		course.sync_status === "error"
			? " is-error"
			: course.sync_status === "warning"
				? " is-warning"
				: "";
	return <span className={`state${tone}`}>{label}</span>;
}

function CourseCard({
	course,
	onChanged,
	onDetached,
	onError,
}: {
	course: AdminCourse;
	onChanged: (course: AdminCourse) => void;
	onDetached: () => void;
	onError: (caught: unknown, fallback: string) => void;
}) {
	const [syncing, setSyncing] = useState(false);
	const [confirmDetach, setConfirmDetach] = useState(false);
	const [rotating, setRotating] = useState<Role | null>(null);
	const [created, setCreated] = useState<CreatedShareLink | null>(null);
	const [busy, setBusy] = useState(false);

	async function syncNow() {
		setSyncing(true);
		try {
			onChanged(await api.sync(course.slug));
		} catch (caught) {
			onError(caught, "The course could not be synced.");
		} finally {
			setSyncing(false);
		}
	}

	async function createLink(role: Role) {
		setBusy(true);
		try {
			const link = await api.createLink(course.slug, role);
			setCreated(link);
			onChanged({
				...course,
				links: [
					...course.links.filter((item) => item.role !== role),
					link.link,
				],
			});
		} catch (caught) {
			onError(caught, "The link could not be created.");
		} finally {
			setBusy(false);
			setRotating(null);
		}
	}

	async function detach() {
		setBusy(true);
		try {
			await api.detach(course.slug);
			onDetached();
		} catch (caught) {
			onError(caught, "The course could not be detached.");
			setBusy(false);
			setConfirmDetach(false);
		}
	}

	return (
		<li className="classroom-card course-card">
			<div className="course-card-header">
				<div>
					<h3>{course.title ?? course.slug}</h3>
					<p className="meta mono">{course.repo_url}</p>
				</div>
				<StatusBadge course={course} />
			</div>
			<dl className="course-facts">
				<dt>Address</dt>
				<dd>
					<a href={`/c/${course.slug}`}>/c/{course.slug}</a>
				</dd>
				<dt>Serving</dt>
				<dd>
					{course.release
						? `${course.release.name} (${course.release.tag})`
						: "No Release yet"}
				</dd>
				<dt>Last sync</dt>
				<dd>{formatTime(course.last_sync_at)}</dd>
			</dl>
			{course.sync_message ? (
				<pre className="sync-message">{course.sync_message}</pre>
			) : null}
			{created ? (
				<CreatedLink link={created} onDone={() => setCreated(null)} />
			) : null}
			<div className="course-actions">
				<button
					type="button"
					className="btn"
					onClick={syncNow}
					disabled={syncing}
				>
					<RefreshCw aria-hidden="true" className={syncing ? "spin" : ""} />
					{syncing ? "Syncing…" : "Sync now"}
				</button>
				{(["learner", "instructor"] as const).map((role) => {
					const exists = course.links.some((link) => link.role === role);
					return (
						<button
							key={role}
							type="button"
							className="btn"
							disabled={busy}
							onClick={() =>
								exists ? setRotating(role) : void createLink(role)
							}
						>
							<Link2 aria-hidden="true" />
							{exists
								? `Rotate ${ROLE_LABEL[role]} link`
								: `Create ${ROLE_LABEL[role]} link`}
						</button>
					);
				})}
				<button
					type="button"
					className="btn btn-quiet"
					onClick={() => setConfirmDetach(true)}
					disabled={busy}
				>
					<Trash2 aria-hidden="true" />
					Detach
				</button>
			</div>
			{rotating ? (
				<ConfirmDialog
					title={`Rotate the ${ROLE_LABEL[rotating]} link?`}
					confirmLabel="Rotate link"
					busy={busy}
					onConfirm={() => void createLink(rotating)}
					onCancel={() => setRotating(null)}
				>
					<p>
						The current {ROLE_LABEL[rotating]} link stops working, and everyone
						who opened it must open the new one.
					</p>
				</ConfirmDialog>
			) : null}
			{confirmDetach ? (
				<ConfirmDialog
					title={`Detach ${course.title ?? course.slug}?`}
					confirmLabel="Detach"
					busy={busy}
					onConfirm={() => void detach()}
					onCancel={() => setConfirmDetach(false)}
				>
					<p>
						Its links stop working and its mirror and served Release are
						removed. The repository itself is not changed.
					</p>
				</ConfirmDialog>
			) : null}
		</li>
	);
}

function CreatedLink({
	link,
	onDone,
}: {
	link: CreatedShareLink;
	onDone: () => void;
}) {
	const [copied, setCopied] = useState(false);
	const inputId = useId();

	async function copy() {
		try {
			await navigator.clipboard.writeText(link.url);
			setCopied(true);
		} catch {
			setCopied(false);
		}
	}

	return (
		<Notice
			tone="success"
			role="status"
			actions={
				<button type="button" className="btn btn-small" onClick={onDone}>
					Done
				</button>
			}
		>
			<div className="field created-link">
				<label htmlFor={inputId}>New {ROLE_LABEL[link.role]} link</label>
				<div className="created-link-row">
					<input
						id={inputId}
						readOnly
						value={link.url}
						onFocus={(event) => event.target.select()}
					/>
					<button type="button" className="btn btn-small" onClick={copy}>
						<Copy aria-hidden="true" />
						{copied ? "Copied" : "Copy"}
					</button>
				</div>
				<small>
					Copy it now: it is shown only once. Share it only with the people it
					is meant for.
				</small>
			</div>
		</Notice>
	);
}
