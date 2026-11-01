import { useCallback, useEffect, useRef, useState } from "react";

import { responseError } from "./api";
import {
	type Candidate,
	candidateResource,
	type DocumentProcessing,
	type DocumentProcessorView,
	type ResourceState,
	type Source,
} from "./models";
import { errorMessage } from "./ui";

export const MATERIAL_ACCEPT =
	".pdf,.pptx,.docx,.md,.markdown,.txt,application/pdf";

export type ModelDownloadStatus = {
	ready: boolean;
	/** Whether full Docling is installed; its models are useless without it. */
	installed: boolean;
	downloading: boolean;
	stage: string | null;
	completed_steps: number;
	total_steps: number;
	error: string | null;
};

export type ModelPrompt =
	| { kind: "process"; resourceId: string; processor: string }
	| { kind: "manual" };

/** A hosted Document Processor waiting for the Course Author to agree to send documents. */
export type ConsentPrompt = {
	resourceId: string | null;
	processor: DocumentProcessorView;
};

/** One file the Course Author added, tracked until it is usable or fails. */
export type UploadItem = {
	id: string;
	name: string;
	include: boolean;
	state: "uploading" | "including" | "failed";
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
	const [consentPrompt, setConsentPrompt] = useState<ConsentPrompt | null>(
		null,
	);
	const [consentError, setConsentError] = useState<string | null>(null);
	const [processing, setProcessing] = useState<DocumentProcessing | null>(null);
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

	const reloadProcessing = useCallback(async () => {
		const response = await fetch("/api/document-processing");
		if (!response.ok) throw new Error(await responseError(response));
		const latest = (await response.json()) as DocumentProcessing;
		setProcessing(latest);
		return latest;
	}, []);

	useEffect(() => {
		void reloadProcessing().catch(() => {
			// Reprocessing reports the problem when the Course Author asks for it.
		});
	}, [reloadProcessing]);

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
		await Promise.all(items.map(uploadOne));
	}

	function retryUpload(id: string) {
		const item = uploads.find((candidate) => candidate.id === id);
		if (!item) return;
		void uploadOne(item);
	}

	function dismissUpload(id: string) {
		updateUpload(id, null);
	}

	async function runProcess(
		resourceId: string,
		reprocess: boolean,
		processor?: string,
	) {
		await withResource(
			resourceId,
			reprocess ? "Reprocessing…" : "Processing…",
			reprocess ? "Reprocessing failed." : "This file could not be processed.",
			async () => {
				const response = await fetch(
					`/api/resources/${encodeURIComponent(resourceId)}/${reprocess ? "reprocess" : "process"}`,
					processor
						? {
								method: "POST",
								headers: { "Content-Type": "application/json" },
								body: JSON.stringify({ processor }),
							}
						: { method: "POST" },
				);
				if (!response.ok) throw new Error(await responseError(response));
				await reloadResources();
			},
		);
	}

	/** Reprocess with a chosen Document Processor, asking first for anything it needs. */
	function reprocessWith(resourceId: string, processorId: string) {
		const processor = processing?.processors.find(
			(candidate) => candidate.id === processorId,
		);
		if (!processor) return runProcess(resourceId, true, processorId);
		if (!processor.available) {
			if (processor.unavailable_action === "download-models") {
				setModelError(null);
				setModelPrompt({ kind: "process", resourceId, processor: processorId });
			} else {
				reportError(
					resourceId,
					processor.unavailable_reason ??
						`${processor.label} is not available.`,
				);
			}
			return Promise.resolve();
		}
		if (processor.hosted && !processor.consented) {
			setConsentError(null);
			setConsentPrompt({ resourceId, processor });
			return Promise.resolve();
		}
		return runProcess(resourceId, true, processorId);
	}

	async function confirmConsent() {
		if (!consentPrompt?.processor.vendor) return;
		const pending = consentPrompt;
		setConsentError(null);
		try {
			const response = await fetch(
				`/api/document-processing/consent/${encodeURIComponent(pending.processor.vendor ?? "")}`,
				{ method: "PUT" },
			);
			if (!response.ok) throw new Error(await responseError(response));
			setProcessing((await response.json()) as DocumentProcessing);
			setConsentPrompt(null);
			if (pending.resourceId)
				await runProcess(pending.resourceId, true, pending.processor.id);
		} catch (caught) {
			setConsentError(
				errorMessage(caught, "Your choice could not be saved. Please retry."),
			);
		}
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
			await reloadProcessing();
			if (pending.kind === "process")
				await runProcess(pending.resourceId, true, pending.processor);
		} catch (caught) {
			setModelError(errorMessage(caught, "Models could not be downloaded."));
		} finally {
			setDownloading(false);
		}
	}

	/** The Document Processors that can read a Resource of this media type. */
	function processorsFor(mediaType: string | null | undefined) {
		if (!mediaType || !processing) return [];
		return processing.processors.filter((processor) =>
			processor.media_types.includes(mediaType),
		);
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
		// The open-access copy usually holds the full text; the landing page may not.
		const url =
			candidateResource(resourcesRef.current, candidate)?.location ??
			candidate.open_access_url ??
			candidate.url;
		await withResource(
			key,
			includeInCourse ? "Adding to course…" : "Saving…",
			"This result could not be added.",
			async () => {
				if (includeInCourse) {
					try {
						const response = await fetch("/api/sources/from-url", {
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({
								url,
								...(candidate.title
									? { label: candidate.title.slice(0, 200) }
									: {}),
							}),
						});
						if (!response.ok) throw new Error(await responseError(response));
						await reloadSources();
					} finally {
						await reloadResources();
					}
					return;
				}
				if (candidateResource(resourcesRef.current, candidate)) return;
				const response = await fetch("/api/resources/remote", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ url }),
				});
				if (!response.ok) throw new Error(await responseError(response));
				await reloadResources();
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
		reprocessWith,
		processorsFor,
		processing,
		setProcessing,
		reloadProcessing,
		consentPrompt,
		consentError,
		requestConsent: (processor: DocumentProcessorView) => {
			setConsentError(null);
			setConsentPrompt({ resourceId: null, processor });
		},
		cancelConsent: () => setConsentPrompt(null),
		confirmConsent,
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
