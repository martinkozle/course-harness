# Taste (Continuously Learned by [CommandCode][cmd])

[cmd]: https://commandcode.ai/

# architecture
- Separate provider accounts (credential storage) from model presets (named model selections) so users can create multiple model presets sharing the same API key. Confidence: 0.80
- Validate API keys at configuration time (e.g., against OpenRouter `/api/v1/key`) rather than failing at runtime during first use. Confidence: 0.70
- Make structured output / JSON mode a non-blocking diagnostic warning rather than a required capability when typed tool calls already provide sufficient validation. Confidence: 0.75

# ui
- Use real button-based view switching rather than hash-only anchor links (`<a href="#...">`) for workspace navigation. Confidence: 0.70

# course-harness
- Treat goals and outcomes as optional fields in the Course Plan model. Confidence: 0.70
- Use typer for CLI argument parsing (not argparse). Confidence: 0.50
- Use uv's native build system (not hatchling or setuptools). Confidence: 0.50

# ui
- Use real button-based view switching rather than hash-only anchor links (`<a href="#...">`) for workspace navigation. Confidence: 0.70
- Enter key should send chat messages; Shift+Enter should insert a newline. Confidence: 0.70

# nix
- Use a Nix flake with direnv for the development environment (system Python, Chromium, Bun, uv, Node.js). Confidence: 0.60
- Use git-hooks.nix for pre-commit hooks (Biome, Ruff, ty, nixfmt, uv-check, common hygiene hooks). Confidence: 0.60

# course-harness
See [course-harness/taste.md](course-harness/taste.md)
# debugging
- Set up FastAPI logging so API errors (422, 500, etc.) produce visible server-side logs with request details, not just silent HTTP status codes. Confidence: 0.60

# ui
- Don't show "no results" placeholder text in search until the user has explicitly submitted a search query. Confidence: 0.70
- When displaying "no results" after a search, show the last-submitted query text (not the current live input value) so the message doesn't change as the user edits the input afterward. Confidence: 0.65
