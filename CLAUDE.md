# Codex familiar footer

This repo contains the reproducible installer and patch. The upstream source checkout is separate. Read README.md and docs/maintenance.md before changing the build or installation workflow.

- upstream.json pins the release version and exact commit. patches/familiar-footer.patch contains only the reviewed Rust changes.
- templates/codex-ui is the per-machine launcher. manage.py installs complete versioned installations and switches relative current/previous symlinks.
- local.json contains private machine paths and is ignored. Keep paths, hostnames, credentials, generated binaries, and build artifacts out of commits.
- install builds in a separate Git worktree. --reuse-build is only for an already built checkout that exactly matches the pin and patch.
- update VERSION must preserve the installed version if patching, building, or helper verification fails. Port the patch when it stops applying; keep the old version usable meanwhile.
- Keep codex-code-mode-host and codex-exec-server beside the custom executable. Their versions must match the pinned release.
- The user reviews runtime behavior before a commit. Never push without an explicit request.

## Whisper dictation

`whisper-dictation/` holds a separate local macOS helper and its installer. It uses the existing MLX Whisper uv tool and cached model. Keep vocabulary files and compiled apps outside the repo. The key toggles recording and submission; Escape cancels. Live previews replace only the text inserted for the recording. Runtime behavior and permission changes require user review before committing. The helper works with the official Codex executable as well as the custom footer build.
