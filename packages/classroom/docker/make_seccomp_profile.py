"""Derive seccomp-classroom.json from Docker's default seccomp profile.

Docker's default profile allows ``unshare`` only with CAP_SYS_ADMIN, so the LibreOffice wrapper
cannot create its network-less sandbox. This adds one rule: ``unshare`` is allowed when the only
namespace flags it asks for are CLONE_NEWUSER and CLONE_NEWNET. Every other namespace (mount,
PID, IPC, UTS, cgroup) still needs CAP_SYS_ADMIN, which the Classroom container drops.

To update, download the default profile of the moby/profiles version your Docker Engine uses (see
``github.com/moby/profiles/seccomp`` in moby's go.mod for that release) and run::

    curl -fsSLo default.json \\
      https://raw.githubusercontent.com/moby/profiles/seccomp/v0.2.3/seccomp/default.json
    python3 packages/classroom/docker/make_seccomp_profile.py default.json \\
      > packages/classroom/docker/seccomp-classroom.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CLONE_NEWNS = 0x00020000
CLONE_NEWCGROUP = 0x02000000
CLONE_NEWUTS = 0x04000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000
CLONE_NEWNET = 0x40000000
# The namespace flags the sandbox must not ask for: (flags & mask) == 0.
FORBIDDEN_NAMESPACES = CLONE_NEWNS | CLONE_NEWCGROUP | CLONE_NEWUTS | CLONE_NEWIPC | CLONE_NEWPID

SANDBOX_RULE = {
    "names": ["unshare"],
    "action": "SCMP_ACT_ALLOW",
    "args": [
        {
            "index": 0,
            "value": FORBIDDEN_NAMESPACES,
            "valueTwo": 0,
            "op": "SCMP_CMP_MASKED_EQ",
        }
    ],
    "comment": (
        "Course Harness Classroom: lets the LibreOffice wrapper unshare a user and a network "
        "namespace only, to render untrusted templates without network access."
    ),
}


def derive(default_profile: dict) -> dict:
    profile = dict(default_profile)
    profile["syscalls"] = [*default_profile["syscalls"], SANDBOX_RULE]
    return profile


def main() -> None:
    default_profile = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    json.dump(derive(default_profile), sys.stdout, indent="\t")
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
