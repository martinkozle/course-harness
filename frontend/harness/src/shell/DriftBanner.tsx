import { AlertTriangle } from "lucide-react";

import type { CurrentState } from "../models";

export function DriftBanner({
	state,
	onReview,
}: {
	state: CurrentState;
	onReview: () => void;
}) {
	return (
		<div className="drift-banner" role="status">
			<AlertTriangle aria-hidden="true" />
			<p>
				{state.drift === "unknown"
					? "This course has no recorded history yet. Review its files before you continue."
					: state.interrupted_run
						? "The Course Agent stopped before it finished. Review the changes it left."
						: "Course files changed outside the app."}
			</p>
			<button type="button" className="btn btn-small" onClick={onReview}>
				Review changes
			</button>
		</div>
	);
}
