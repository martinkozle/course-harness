#!/usr/bin/env bash
# Enter the pinned development shell, also where GitHub archive downloads are blocked.
#
# `nix develop` fetches the flake's GitHub inputs as release archives
# (github.com/<owner>/<repo>/archive/<rev>.tar.gz). Some sandboxes, such as cloud agent
# containers, allow `git clone` of GitHub but refuse those archive downloads. Then this
# script overrides each GitHub input with a shallow git fetch of the exact revision that
# flake.lock pins, so the shell is the same one `nix develop` would build.
#
#   scripts/nix-develop.sh                         # interactive shell
#   scripts/nix-develop.sh -c uv run pytest        # one command
#   scripts/nix-develop.sh -c pre-commit run --all-files
#
# Set COURSE_HARNESS_NIX_GIT_INPUTS=1 to skip the archive probe and always use git, or =0
# to always use plain `nix develop`.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v nix >/dev/null 2>&1; then
	echo "nix is not installed. See https://nixos.org/download/ (enable flakes)." >&2
	exit 1
fi

nix_flags=(--extra-experimental-features "nix-command flakes")

# One "<input path> <owner> <repo> <rev>" line per GitHub input in flake.lock. A nested input
# is addressed by its path from the root, such as git-hooks/flake-compat; inputs that only
# follow another input are skipped, because overriding their target covers them.
github_inputs="$(
	nix "${nix_flags[@]}" eval --impure --raw --expr '
		let
			lock = builtins.fromJSON (builtins.readFile ./flake.lock);
			inputsOf = id: lock.nodes.${id}.inputs or { };
			walk = prefix: id: builtins.concatLists (map (name:
				let target = (inputsOf id).${name};
				in if builtins.isString target then
					let
						path = if prefix == "" then name else "${prefix}/${name}";
						locked = lock.nodes.${target}.locked or { };
						here = if (locked.type or "") == "github"
							then [ "${path} ${locked.owner} ${locked.repo} ${locked.rev}" ]
							else [ ];
					in here ++ walk path target
				else [ ]) (builtins.attrNames (inputsOf id)));
		in
			builtins.concatStringsSep "\n" (walk "" lock.root)
	'
)"

use_git="${COURSE_HARNESS_NIX_GIT_INPUTS:-}"
if [[ -z "$use_git" ]]; then
	# Probe the first input's archive; a refusal means every archive download will fail.
	read -r _ owner repo rev <<<"$(head -n 1 <<<"$github_inputs")"
	if curl --silent --show-error --fail --location --head --max-time 20 \
		"https://github.com/$owner/$repo/archive/$rev.tar.gz" >/dev/null 2>&1; then
		use_git=0
	else
		use_git=1
	fi
fi

overrides=()
if [[ "$use_git" == "1" ]]; then
	echo "GitHub archives are unreachable; fetching flake inputs with git." >&2
	while read -r input owner repo rev; do
		[[ -n "$input" ]] || continue
		overrides+=(--override-input "$input" "git+https://github.com/$owner/$repo?rev=$rev&shallow=1")
	done <<<"$github_inputs"
	# The overrides pin the same revisions, so flake.lock must stay untouched.
	overrides+=(--no-write-lock-file)
fi

exec nix "${nix_flags[@]}" develop "${overrides[@]}" "$@"
