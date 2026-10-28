# Course Harness

[![PyPI](https://img.shields.io/pypi/v/course-harness)](https://pypi.org/project/course-harness/)
[![Python](https://img.shields.io/pypi/pyversions/course-harness)](https://pypi.org/project/course-harness/)
[![License: MIT](https://img.shields.io/pypi/l/course-harness)](https://github.com/martinkozle/course-harness/blob/main/LICENSE)

Course Harness is a local-first application for creating source-grounded Courses in a
human-readable Course Workspace. You plan the Course with a Course Agent, ground it in papers and
documents you choose, and get Lectures with speaker notes and citations that export to PowerPoint
in your own template.

![A Course Agent conversation beside a cited Lecture Slide](https://raw.githubusercontent.com/martinkozle/course-harness/main/docs/images/workspace.png)

## Install and launch

Install [uv](https://docs.astral.sh/uv/). It downloads Python 3.14 if you do not have it.

```bash
uvx 'course-harness[pinned]'
```

Or install it as a command-line tool:

```bash
uv tool install 'course-harness[pinned]'
course-harness
```

The `pinned` extra installs exactly the dependency versions each release was tested with, so an
install keeps working as newer dependencies come out.

The command opens `http://127.0.0.1:8765` with the Workspace Launcher, where you create or open a
Course Workspace. Useful options:

```bash
course-harness /path/to/course-workspace  # open a Workspace directly (`.` works too)
course-harness --no-browser               # do not open the browser
course-harness --port 9000                # use another local port
```

## Set up a model

Open **Models** to add a provider. Course Harness works with OpenRouter, OpenAI, Anthropic, Amazon
Bedrock, and any OpenAI-compatible server. Under **Found on this computer**, it lists the provider
credentials it can see and lets you add each one with a single click. It looks at
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, AWS session
variables (such as those set by awsume), and the profiles in `~/.aws`. None of these is used until
you add it.

For Bedrock, an AWS profile is the most robust choice: only its name is stored, and the AWS SDK
refreshes SSO and role credentials by itself. Session variables are read from the shell that
started Course Harness, so after they expire, refresh them and restart from that shell. Prompt
caching is turned on for Anthropic and OpenRouter models, and for Bedrock models that support it.

To use an OpenAI-compatible provider on another machine over plain HTTP, select **Allow HTTP to
another host**. The API key and Course content are then sent unencrypted, so use it only on a
network you trust.

## What it does

- **Sources**: add PDF, `.pptx`, `.docx`, and web pages, or discover papers through arXiv,
  Crossref, Semantic Scholar, and OpenAlex. Documents are read quickly on your device first. When
  a document looks scanned, image-heavy, or garbled, you can reprocess it with full Docling (an
  optional extra) or a hosted processor: LlamaParse, Mistral OCR, or one of your Model Presets.
  A hosted processor gets a document only after you agree to send documents to that vendor.
- **Course Agent**: plans the Course, researches sources, and writes Lectures. Only Sources added
  to the Course can be cited.
- **Connectors**: web search and page reading through remote MCP servers. Exa is set up by default
  and works without a key.
- **History**: save and restore Course Revisions, publish Releases, and export Lectures to
  PowerPoint.

<table>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/martinkozle/course-harness/main/docs/images/slides.png" alt="A Slide with its purpose, speaker notes, and citations"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/martinkozle/course-harness/main/docs/images/sources.png" alt="The Sources a Course can cite"></td>
  </tr>
  <tr>
    <td align="center">Slides with purpose, speaker notes, and citations</td>
    <td align="center">Sources the Course Agent can read and cite</td>
  </tr>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/martinkozle/course-harness/main/docs/images/course-plan.png" alt="A Course Plan with three Lectures"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/martinkozle/course-harness/main/docs/images/history.png" alt="Course Revisions in History"></td>
  </tr>
  <tr>
    <td align="center">The Course Plan</td>
    <td align="center">Course Revisions you can restore</td>
  </tr>
</table>

Canonical Course state lives in readable files in the Course Workspace. Caches, conversations, and
credentials live in platform-native user locations outside it; credentials go to the operating
system keyring when one is available. **Settings → Diagnostics** shows the exact locations.

## Optional extras

**Full Docling for difficult PDFs.** For scanned PDFs, tables, and multi-column layouts you can
reprocess a document with full Docling, which runs layout, table, and text recognition models
locally:

```bash
uv tool install --torch-backend cpu 'course-harness[pinned,docling]'
```

`--torch-backend cpu` installs the CPU build of PyTorch. Without it, uv installs the CUDA build
on Linux, which downloads several gigabytes of GPU libraries. Course Harness asks before it
downloads the Docling models.

**LibreOffice** (`libreoffice` or `soffice` on `PATH`) gives high-fidelity slide thumbnails.

## Development

See [CONTRIBUTING.md](https://github.com/martinkozle/course-harness/blob/main/CONTRIBUTING.md)
to run Course Harness from a checkout, run the checks, and publish a release.

## License

[MIT](https://github.com/martinkozle/course-harness/blob/main/LICENSE)
