import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
	a: ({ node: _node, ...props }) => (
		<a {...props} target="_blank" rel="noopener noreferrer" />
	),
	table: ({ node: _node, ...props }) => (
		<div className="md-table">
			<table {...props} />
		</div>
	),
};

/** Renders agent Markdown. Raw HTML is never interpreted. */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
	return (
		<div className="md">
			<ReactMarkdown
				remarkPlugins={[remarkGfm]}
				components={components}
				skipHtml
			>
				{text}
			</ReactMarkdown>
		</div>
	);
});
