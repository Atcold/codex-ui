# Familiar footer

The patch adds four optional footer items. Use the commands in README.md for routine installation and updates; the source-build details below are for maintaining the patch. The consumed-context item uses the compact label `N% ctx`.

- `model-with-host` shows the model followed by `@machine`. On macOS, the machine name comes from `scutil --get ComputerName`; other platforms use the hostname. The model is purple, `@` is white, and the machine is blue.
- `current-dir-name` shows the final component of the working directory in yellow, including in project homes outside a Git repository. The filesystem root falls back to its path.
- `five-hour-limit-bar` shows the consumed five-hour allowance with ten boxes and its local reset time.
- `weekly-limit-bar` shows the consumed weekly allowance with ten boxes and its local reset time.

Each filled box represents approximately ten percent consumed, rounded to the nearest box. Quota fields use Codex's account limits. They are allowances rather than exact token counts. Bars turn yellow at four filled boxes, orange at seven, and red at nine. Context and quota text are dimmed; the model, machine, and directory retain their bright colors. Turning off theme colors preserves the text and boxes without the custom colors.

```toml
[tui]
status_line = ["model-with-host", "current-dir-name", "git-branch", "context-used", "five-hour-limit-bar"]
```

The result reads as `model@machine project · branch · context · quota`. Selecting `current-dir` instead keeps the full path available. The model and machine update with the active model and the computer running the UI; the folder follows the active working directory.

Build from `codex-rs/` with `cargo build --bin codex --profile dev --config profile.dev.debug=0 --config profile.dev.incremental=false`. The executable is `codex-rs/target/debug/codex`. Use the normal shared daemon when switching between packaged and custom clients; forcing a separate server with `--no-daemon` can conflict with an existing conversation writer. Keep the packaged executable available for rollback. Rebase the `familiar-footer` branch onto a new release, resolve any changes to the status surface modules, and rebuild to update the custom executable.

The local launcher supplies the custom footer items as command-line overrides, so the packaged executable can still use the ordinary configuration. Each machine keeps its launcher and versioned executable folders in the XDG data directory, with build artifacts in the XDG cache directory and builds from this repository. Shared Codex configuration and conversation state remain separate from that installation.

The release tag sets the workspace version to `0.166.0` while its lockfile still records local packages as `0.0.0`. The initial build updates that version metadata; external dependency versions stay pinned. Rust 1.95.0 and CMake are required for this release.

Each versioned installation is a directory containing the patched executable and the matching `codex-code-mode-host` and `codex-exec-server` helpers beside the custom executable. Copy those helpers from the same packaged release when installing the custom build.

The default footer shows only the five-hour quota, without a window label. Reset times use 24-hour `H:mm` format without a date. The weekly bar remains an optional field.

The existing double-press quit behavior is enabled. With an empty composer and no open modal, Ctrl-D arms a one-second confirmation window; a second Ctrl-D within that window exits. Ctrl-C shares the same switch while retaining its existing interruption behavior.

The main transcript accepts Option-modified page keys. The launcher binds `alt-page-up` and `alt-page-down` to the pager’s single-row actions while preserving their existing arrow and vi bindings. Plain page keys retain their full-page movement.
