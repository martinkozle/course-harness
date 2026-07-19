# Start unbound and open Workspaces explicitly

Launching Course Harness without a path opens a restricted Workspace Launcher rather than silently
using the current directory or an application-managed home directory. The Course Author may create
a Course Workspace, open a folder, or choose a recent Workspace; passing an explicit CLI path
bypasses the launcher. The process has at most one active Course Workspace after selection, stores
recent-Workspace metadata outside Course folders, and exposes native folder selection rather than a
general browser-facing filesystem API. This preserves explicit filesystem authority and portable,
visible Course folders while supporting a routine workflow that does not require terminal knowledge.
