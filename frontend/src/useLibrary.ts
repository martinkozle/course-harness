import { useCallback, useEffect, useRef, useState } from "react";

import { responseError } from "./api";
import type { Candidate, ResourceState, Source } from "./models";
import { errorMessage } from "./ui";

export const MATERIAL_ACCEPT =
	".pdf,.pptx,.docx,.md,.markdown,.txt,application/pdf";

export type ModelDownloadStatus = {
	ready: boolean;
	downloading: boolean;
	stage: string | null;
	completed_steps: number;
	total_steps: number;
	error: string | null;
};

export type ModelPrompt =
	| { kind: "upload"; files: File[]; include: boolean }
	| { kind: "process"; resourceId: string; reprocess: boolean }
	| { kind: "manual" };

/** One file the Course Author added, tracked until it is usable or fails. */
export type UploadItem = {
	id: string;
	name: string;
	include: boolean;
	state: "uploading" | "including" | "needs-models" | "failed";
	error: string | null;
	file: File;
};

type Options = {
	resources: ResourceState[];
	sources: Source[];
	onResourcesChange: (resources: ResourceState[]) => void;
	onSourcesChange: (sources: Source[]) => void;
};

export function resourceName(resource: ResourceState): string {
	const location = resource.location ?? resource.resource_id;
	if (resource.kind === "remote") return location;
	return location.split(/[\\/]/).at(-1) || location;
}

export function useLibrary({
	resources,
	sources,
	onResourcesChange,
	onSourcesChange,
}: Options) {
	const [uploads, setUploads] = useState<UploadItem[]>([]);
	const [busy, setBusy] = useState<Map<string, string>>(new Map());
	const [errors, setErrors] = useState<Map<string, string>>(new Map());
	const [modelPrompt, setModelPrompt] = useState<ModelPrompt | null>(null);
	const [modelStatus, setModelStatus] = useState<ModelDownloadStatus | null>(
		null,
	);
	const [downloading, setDownloading] = useState(false);
	const [modelError, setModelError] = useState<string | null>(null);
	const [regenerating, setRegenerating] = useState(false);
	const [indexError, setIndexError] = useState<string | null>(null);
	const resourcesRef = useRef(resources);
	resourcesRef.current = resources;

	const reloadResources = useCallback(async () => {
		const response = await fetch("/api/resources");
		if (!response.ok) throw new Error(await responseError(response));
		const latest = (await response.json()) as ResourceState[];
		onResourcesChange(latest);
		return latest;
	}, [onResourcesChange]);

	const reloadSources = useCallback(async () => {
		const response = await fetch("/api/sources");
		if (!response.ok) throw new Error(await responseError(response));
		const latest = (await response.json()) as Source[];
		onSourcesChange(latest);
		return latest;
	}, [onSourcesChange]);

	const hasRemoteProcessing = resources.some(
		(resource) =>
			resource.kind === "remote" && resource.status === "processing",
	);

	useEffect(() => {
		if (!hasRemoteProcessing) return;
		const timer = window.setInterval(() => {
			void reloadResources().catch(() => {
				// The next poll recovers from a transient failure.
			});
		}, 1000);
		return () => window.clearInterval(timer);
	}, [hasRemoteProcessing, reloadResources]);

	useEffect(() => {
		let active = true;
		async function refresh() {
			try {
				const response = await fetch("/api/resources/parser-models");
				if (!response.ok) return;
				const next = (await response.json()) as ModelDownloadStatus;
				if (active) setModelStatus(next);
			} catch {
				// A manual action reports request errors when selected.
			}
		}
		void refresh();
		if (!downloading) {
			return () => {
				active = false;
			};
		}
		const timer = window.setInterval(() => void refresh(), 500);
		return () => {
			active = false;
			window.clearInterval(timer);
		};
	}, [downloading]);

	function mark(id: string, label: string | null) {
		setBusy((current) => {
			const next = new Map(current);
			if (label) next.set(id, label);
			else next.delete(id);
			return next;
		});
	}

	function reportError(id: string, message: string | null) {
		setErrors((current) => {
			const next = new Map(current);
			if (message) next.set(id, message);
			else next.delete(id);
			return next;
		});
	}

	async function withResource(
		id: string,
		label: string,
		fallback: string,
		action: () => Promise<void>,
	) {
		mark(id, label);
		reportError(id, null);
		try {
			await action();
		} catch (caught) {
			reportError(id, errorMessage(caught, fallback));
		} finally {
			mark(id, null);
		}
	}

	function updateUpload(id: string, patch: Partial<UploadItem> | null) {
		setUploads((current) =>
			patch === null
				? current.filter((item) => item.id !== id)
				: current.map((item) =>
						item.id === id ? { ...item, ...patch } : item,
					),
		);
	}

	async function admit(resourceId: string, label: string) {
		const response = await fetch("/api/sources", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				resource_id: resourceId,
				label: label.slice(0, 200),
			}),
		});
		if (!response.ok) throw new Error(await responseError(response));
		await reloadSources();
	}

	async function uploadOne(item: UploadItem) {
		updateUpload(item.id, { state: "uploading", error: null });
		try {
			const formData = new FormData();
			formData.append("file", item.file);
			const response = await fetch("/api/resources/upload", {
				method: "POST",
				body: formData,
			});
			if (!response.ok) throw new Error(await responseError(response));
			const uploaded = (await response.json()) as ResourceState;
			await reloadResources();
			if (item.include) {
				if (uploaded.status !== "ready") {
					throw new Error(
						uploaded.error ??
							"The file was saved to your Library but could not be read yet.",
					);
				}
				updateUpload(item.id, { state: "including" });
				await admit(uploaded.resource_id, resourceName(uploaded));
			}
			updateUpload(item.id, null);
		} catch (caught) {
			updateUpload(item.id, {
				state: "failed",
				error: errorMessage(caught, "The file could not be added."),
			});
		}
	}

	async function checkModels(): Promise<boolean> {
		const response = await fetch("/api/resources/parser-models");
		if (!response.ok) throw new Error(await responseError(response));
		const next = (await response.json()) as ModelDownloadStatus;
		setModelStatus(next);
		return next.ready;
	}

	async function addFiles(files: File[], include: boolean) {
		if (files.length === 0) return;
		const items: UploadItem[] = files.map((file) => ({
			id: crypto.randomUUID(),
			name: file.name,
			include,
			state: "uploading",
			error: null,
			file,
		}));
		setUploads((current) => [...current, ...items]);
		const pdfs = items.filter((item) =>
			item.name.toLowerCase().endsWith(".pdf"),
		);
		let modelsReady = true;
		if (pdfs.length > 0) {
			try {
				modelsReady = await checkModels();
			} catch (caught) {
				for (const item of pdfs)
					updateUpload(item.id, {
						state: "failed",
						error: errorMessage(caught, "PDF support could not be checked."),
					});
				modelsReady = false;
			}
		}
		const waiting = modelsReady ? [] : pdfs;
		for (const item of waiting)
			updateUpload(item.id, { state: "needs-models", error: null });
		if (waiting.length > 0) {
			setModelError(null);
			setModelPrompt({
				kind: "upload",
				files: waiting.map((item) => item.file),
				include,
			});
		}
		await Promise.all(
			items.filter((item) => !waiting.includes(item)).map(uploadOne),
		);
	}

	function retryUpload(id: string) {
		const item = uploads.find((candidate) => candidate.id === id);
		if (!item) return;
		if (item.state === "needs-models") {
			setModelError(null);
			setModelPrompt({
				kind: "upload",
				files: [item.file],
				include: item.include,
			});
			return;
		}
		void uploadOne(item);
	}

	function dismissUpload(id: string) {
		updateUpload(id, null);
	}

	async function runProcess(resourceId: string, reprocess: boolean) {
		await withResource(
			resourceId,
			reprocess ? "Reprocessing…" : "Processing…",
			reprocess ? "Reprocessing failed." : "This file could not be processed.",
			async () => {
				const response = await fetch(
					`/api/resources/${encodeURIComponent(resourceId)}/${reprocess ? "reprocess" : "process"}`,
					{ method: "POST" },
				);
				if (response.status === 409) {
					setModelError(null);
					setModelPrompt({ kind: "process", resourceId, reprocess });
					return;
				}
				if (!response.ok) throw new Error(await responseError(response));
				await reloadResources();
			},
		);
	}

	async function confirmModelDownload() {
		if (!modelPrompt) return;
		const pending = modelPrompt;
		setDownloading(true);
		setModelError(null);
		try {
			const response = await fetch("/api/resources/parser-models", {
				method: "POST",
			});
			if (!response.ok) throw new Error(await responseError(response));
			setModelStatus((await response.json()) as ModelDownloadStatus);
			setModelPrompt(null);
			if (pending.kind === "upload") {
				const waiting = uploads.filter(
					(item) =>
						item.state === "needs-models" && pending.files.includes(item.file),
				);
				await Promise.all(waiting.map(uploadOne));
			} else if (pending.kind === "process") {
				await runProcess(pending.resourceId, pending.reprocess);
			}
		} catch (caught) {
			setModelError(errorMessage(caught, "Models could not be downloaded."));
		} finally {
			setDownloading(false);
		}
	}

	function include(resourceId: string) {
		const resource = resourcesRef.current.find(
			(item) => item.resource_id === resourceId,
		);
		if (!resource) return Promise.resolve();
		return withResource(
			resourceId,
			"Adding…",
			"This file could not be added to the course.",
			() => admit(resourceId, resourceName(resource)),
		);
	}

	function removeFromCourse(sourceId: string) {
		const source = sources.find((item) => item.id === sourceId);
		if (!source) return Promise.resolve();
		return withResource(
			source.resource_id,
			"Removing…",
			"This Source could not be removed from the course.",
			async () => {
				const response = await fetch(
					`/api/sources/${encodeURIComponent(sourceId)}`,
					{ method: "DELETE" },
				);
				if (!response.ok) throw new Error(await responseError(response));
				await reloadSources();
			},
		);
	}

	function deleteResource(resourceId: string) {
		return withResource(
			resourceId,
			"Deleting…",
			"This file could not be deleted.",
			async () => {
				const response = await fetch(
					`/api/resources/${encodeURIComponent(resourceId)}`,
					{ method: "DELETE" },
				);
				if (!response.ok) throw new Error(await responseError(response));
				await reloadResources();
			},
		);
	}

	function refreshRemote(resourceId: string) {
		return withResource(
			resourceId,
			"Fetching…",
			"This file could not be fetched again.",
			async () => {
				const response = await fetch(
					`/api/resources/${encodeURIComponent(resourceId)}/refresh`,
					{ method: "POST" },
				);
				if (!response.ok) throw new Error(await responseError(response));
				await reloadResources();
			},
		);
	}

	function adoptVersion(sourceId: string) {
		const source = sources.find((item) => item.id === sourceId);
		if (!source) return Promise.resolve();
		return withResource(
			source.resource_id,
			"Updating…",
			"The newer version could not be used.",
			async () => {
				const response = await fetch(
					`/api/sources/${encodeURIComponent(sourceId)}/adopt-version`,
					{ method: "POST" },
				);
				if (!response.ok) throw new Error(await responseError(response));
				await reloadSources();
			},
		);
	}

	async function addRemote(candidate: Candidate, includeInCourse: boolean) {
		const key = candidate.url;
		await withResource(
			key,
			"Adding…",
			"This result could not be added.",
			async () => {
				const existing = resourcesRef.current.find(
					(resource) => resource.location === candidate.url,
				);
				let resourceId = existing?.resource_id;
				if (!resourceId) {
					const response = await fetch("/api/resources/remote", {
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ url: candidate.url }),
					});
					if (!response.ok) throw new Error(await responseError(response));
					resourceId = ((await response.json()) as ResourceState).resource_id;
				}
				const latest = await reloadResources();
				if (!includeInCourse) return;
				const resource = latest.find((item) => item.resource_id === resourceId);
				if (resource?.status !== "ready") {
					throw new Error(
						"Saved to your Library. Add it to the course once it has finished downloading.",
					);
				}
				await admit(resourceId, candidate.title ?? resourceName(resource));
			},
		);
	}

	async function regenerateIndex() {
		setRegenerating(true);
		setIndexError(null);
		try {
			const response = await fetch("/api/resources/cache", {
				method: "DELETE",
			});
			if (!response.ok) throw new Error(await responseError(response));
			await reloadResources();
		} catch (caught) {
			setIndexError(
				errorMessage(caught, "The search index could not be rebuilt."),
			);
		} finally {
			setRegenerating(false);
		}
	}

	return {
		uploads,
		addFiles,
		retryUpload,
		dismissUpload,
		busy,
		errors,
		clearError: (id: string) => reportError(id, null),
		include,
		removeFromCourse,
		deleteResource,
		process: (id: string) => runProcess(id, false),
		reprocess: (id: string) => runProcess(id, true),
		refreshRemote,
		adoptVersion,
		addRemote,
		regenerateIndex,
		regenerating,
		indexError,
		modelPrompt,
		modelStatus,
		modelError,
		downloading,
		requestModelDownload: () => {
			setModelError(null);
			setModelPrompt({ kind: "manual" });
		},
		cancelModelPrompt: () => setModelPrompt(null),
		confirmModelDownload,
	};
}

export type Library = ReturnType<typeof useLibrary>;
