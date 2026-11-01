import { Dialog, Notice } from "../ui";
import type { Library } from "../useLibrary";

/** Ask once per vendor before a hosted Document Processor receives any document. */
export function ProcessorConsentDialog({ library }: { library: Library }) {
	const prompt = library.consentPrompt;
	if (!prompt) return null;
	const { processor } = prompt;
	return (
		<Dialog
			title={`Send documents to ${processor.label}?`}
			onClose={library.cancelConsent}
			footer={
				<>
					<button type="button" className="btn" onClick={library.cancelConsent}>
						Cancel
					</button>
					<button
						type="button"
						className="btn btn-primary"
						onClick={() => void library.confirmConsent()}
					>
						{prompt.resourceId ? "Send and reprocess" : "Allow"}
					</button>
				</>
			}
		>
			<p>{processor.description}</p>
			<p>
				The whole file leaves this computer and is processed by the vendor under
				its own terms and retention policy. Course Harness keeps the result, so
				the file is not sent again for the same version.
			</p>
			<p className="meta">
				You decide once for this vendor. You can withdraw it in Settings →
				Documents; files already processed keep their text.
			</p>
			{library.consentError ? (
				<Notice tone="error">{library.consentError}</Notice>
			) : null}
		</Dialog>
	);
}
