{
  description = "Course Harness development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    git-hooks.url = "github:cachix/git-hooks.nix";
    git-hooks.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      git-hooks,
    }:
    flake-utils.lib.eachSystem
      [
        "x86_64-linux"
        "aarch64-linux"
      ]
      (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          hookExcludes = [
            "^\\.agents/"
            "^src/course_harness/static/"
          ];
          commonHooks = {
            biome = {
              enable = true;
              entry = "${pkgs.biome}/bin/biome lint";
              files = "^(frontend/|playwright\\.config\\.ts$|tests/e2e/)";
            };
            check-added-large-files.enable = true;
            check-case-conflicts.enable = true;
            check-executables-have-shebangs.enable = true;
            check-json.enable = true;
            check-merge-conflicts.enable = true;
            check-python.enable = true;
            check-shebang-scripts-are-executable.enable = true;
            check-symlinks.enable = true;
            check-toml.enable = true;
            check-yaml.enable = true;
            detect-private-keys.enable = true;
            end-of-file-fixer.enable = true;
            fix-byte-order-marker.enable = true;
            forbid-new-submodules.enable = true;
            mixed-line-endings.enable = true;
            nixfmt.enable = true;
            ruff.enable = true;
            ruff-format.enable = true;
            trim-trailing-whitespace.enable = true;
          };
          preCommitCheck = git-hooks.lib.${system}.run {
            src = ./.;
            excludes = hookExcludes;
            hooks = commonHooks;
          };
          developmentPreCommitCheck = git-hooks.lib.${system}.run {
            src = ./.;
            excludes = hookExcludes;
            hooks = commonHooks // {
              ty = {
                enable = true;
                name = "ty";
                entry = "${pkgs.ty}/bin/ty check";
                files = "\\.py$";
                pass_filenames = false;
                package = pkgs.ty;
              };
              uv-check.enable = true;
            };
          };
        in
        {
          checks.pre-commit-check = preCommitCheck;

          devShells.default = pkgs.mkShell {
            packages =
              (with pkgs; [
                bun
                chromium
                git
                imagemagick
                libreoffice
                nodejs_24
                python314
                uv
                zenity
              ])
              ++ developmentPreCommitCheck.enabledPackages;

            PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH = "${pkgs.chromium}/bin/chromium";
            PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1";
            UV_PYTHON = "${pkgs.python314}/bin/python3.14";
            UV_PYTHON_DOWNLOADS = "never";

            shellHook = ''
              ${developmentPreCommitCheck.shellHook}
              export UV_CACHE_DIR="$PWD/.cache/uv"
            '';
          };

          formatter = pkgs.nixfmt;
        }
      );
}
