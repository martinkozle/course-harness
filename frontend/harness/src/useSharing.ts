import { useCallback, useEffect, useRef, useState } from "react";

import { responseError } from "./api";
import type { SharingStatus, SyncResult } from "./models";

/** How often the remote is asked what it has while a Course is open. */
const CHECK_INTERVAL_MS = 5 * 60 * 1000;

export type Sharing = {
	status: SharingStatus | null;
	checking: boolean;
	syncing: boolean;
	/** The last Sync that did not finish, until it is dismissed or retried. */
	failure: SyncResult | null;
	refresh: () => Promise<void>;
	check: () => Promise<void>;
	sync: () => Promise<SyncResult | null>;
	dismissFailure: () => void;
};

/** Publish and Sync status, kept current in the background. */
export function useSharing(enabled: boolean): Sharing {
	const [status, setStatus] = useState<SharingStatus | null>(null);
	const [checking, setChecking] = useState(false);
	const [syncing, setSyncing] = useState(false);
	const [failure, setFailure] = useState<SyncResult | null>(null);
	const remoteRef = useRef(false);
	const busyRef = useRef(false);

	const accept = useCallback((next: SharingStatus) => {
		remoteRef.current = next.remote_url !== null;
		setStatus(next);
	}, []);

	const refresh = useCallback(async () => {
		try {
			const response = await fetch("/api/workspace/sharing");
			if (response.ok) accept((await response.json()) as SharingStatus);
		} catch {
			// The controls keep their last known state.
		}
	}, [accept]);

	const check = useCallback(async () => {
		if (!remoteRef.current || busyRef.current) return;
		busyRef.current = true;
		setChecking(true);
		try {
			const response = await fetch("/api/workspace/remote/check", {
				method: "POST",
			});
			if (response.ok) accept((await response.json()) as SharingStatus);
		} catch {
			// A failed check shows in the Sync tooltip on the next status.
		} finally {
			busyRef.current = false;
			setChecking(false);
		}
	}, [accept]);

	const sync = useCallback(async () => {
		if (busyRef.current) return null;
		busyRef.current = true;
		setSyncing(true);
		setFailure(null);
		try {
			const response = await fetch("/api/workspace/sync", { method: "POST" });
			if (!response.ok) {
				const result: SyncResult = {
					ok: false,
					message: await responseError(response),
					detail: null,
					findings: [],
					revision: null,
					received: 0,
					status: null,
				};
				setFailure(result);
				return result;
			}
			const result = (await response.json()) as SyncResult;
			if (result.status) accept(result.status);
			if (!result.ok) setFailure(result);
			return result;
		} catch {
			const result: SyncResult = {
				ok: false,
				message: "Course Harness could not start Sync.",
				detail: null,
				findings: [],
				revision: null,
				received: 0,
				status: null,
			};
			setFailure(result);
			return result;
		} finally {
			busyRef.current = false;
			setSyncing(false);
		}
	}, [accept]);

	useEffect(() => {
		if (!enabled) return;
		void refresh().then(check);
		const onFocus = () => void check();
		window.addEventListener("focus", onFocus);
		const timer = window.setInterval(() => void check(), CHECK_INTERVAL_MS);
		return () => {
			window.removeEventListener("focus", onFocus);
			window.clearInterval(timer);
		};
	}, [enabled, refresh, check]);

	return {
		status,
		checking,
		syncing,
		failure,
		refresh,
		check,
		sync,
		dismissFailure: () => setFailure(null),
	};
}
