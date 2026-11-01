import { ModelSettings } from "../ProviderSetup";
import type { ModelCatalog } from "../models";
import { Dialog } from "../ui";

/** The Settings model form, opened in place from the composer. */
export function ModelSetupDialog({
	catalog,
	onCatalogChange,
	onClose,
}: {
	catalog: ModelCatalog;
	onCatalogChange: (catalog: ModelCatalog) => void;
	onClose: () => void;
}) {
	return (
		<Dialog title="Add a model" onClose={onClose} size="wide">
			<p className="meta">
				Connect a provider once, then name the model the Course Agent should
				use. Your message draft is kept.
			</p>
			<ModelSettings
				catalog={catalog}
				onCatalogChange={onCatalogChange}
				onPresetSaved={onClose}
			/>
		</Dialog>
	);
}
