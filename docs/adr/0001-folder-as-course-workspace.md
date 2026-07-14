# Use a folder as the bounded course workspace

The app opens or starts in a directory that becomes the Course Workspace, keeping human-readable course state and artifacts visible while storing derived extraction, indexes, and runtime metadata under `.course-harness/`. The agent may read within the workspace and write managed course paths, but it must not overwrite pre-existing files or access outside paths without explicit approval; this makes the course portable and inspectable without giving an autonomous agent ambient filesystem authority.
