# course-harness
- Treat goals and outcomes as optional fields in the Course Plan model. Confidence: 0.70
- Use typer for CLI argument parsing (not argparse). Confidence: 0.50
- Use uv's native build system (not hatchling or setuptools). Confidence: 0.50
- Include surrounding line context in FTS5 search result snippets, not just the single matching line. Confidence: 0.55
- Group search results by source, order sources by maximum chunk similarity, then list chunks within each source sorted by line number. Confidence: 0.70
- Process uploaded documents eagerly by default rather than deferring to lazy processing triggered by search. Confidence: 0.60
- Use explicit action labels like "Reprocess all" instead of ambiguous terms like "Clear cache". Confidence: 0.60
- Surface backend error messages from Process/Reprocess operations in the resource card UI rather than silently failing on 422 responses. Confidence: 0.65
