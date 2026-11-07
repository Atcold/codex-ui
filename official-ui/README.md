# Footer for the official Codex CLI

This Python PTY wrapper runs the installed official `codex` executable and replaces its native footer row with the familiar model, machine, directory, context percentage, consumed-quota boxes, and reset time. It preserves the signed executable and its helpers.

Install with `python3 official-ui/install.py`, then start `codex-ui` through the usual command or alias. It requires `uv` and installs `pyte` in a local virtual environment. For a session that currently requires the daemon bypass, keep using `codex-ui --no-daemon resume`.

The wrapper obtains model, project, branch, context, quota, and run state through Codex’s configured terminal-title fields. It consumes that metadata and leaves a short project/state title visible. The official status line supplies a short version marker that identifies the row to replace, including while the inline viewport moves. Other terminal output and input pass through. The replacement restores the native cursor position and current text attributes.

The model, machine, and directory are bold; context and quota boxes are dimmed. The five-hour bar includes an elapsed-time `│` marker at the nearest box boundary. Its colour follows Claude’s projected-usage spectrum, with a neutral marker during the first 30 minutes.

Quota windows come from the official client’s read-only `account/rateLimits/read` app-server RPC, refreshed every 90 seconds. The weekly quota appears beside the five-hour quota only when its consumption exceeds the elapsed share of the week, compared before rounding the boxes. It uses the same dim boxes and pace marker, with its local reset weekday and time. Percentage values come from the active UI. If the usage read is unavailable, the percentage and boxes remain while the reset time is omitted.

On a recognized empty, ready prompt, the first Ctrl-D arms quitting for one second and adds a footer hint. A second Ctrl-D in that interval reaches Codex and quits. Nonempty prompts, dialogs, and working states retain native Ctrl-D behavior. Other input cancels the armed state. Unknown prompt layouts retain the native behavior.

The installer switches only the local `codex-ui` launcher, preserving its previous contents as `codex-ui-patched` beside it. Invoke that saved launcher to use the previous Rust build. Reinstalling the Rust bundle with `manage.py` also restores its launcher. The wrapper honors `CODEX_UI_OFFICIAL_BINARY` when an explicit official executable is needed. Help, version, and noninteractive commands pass directly to Codex.

The official executable remains responsible for computer-use authentication. App access and navigation have been verified through the wrapper. Footer appearance, resizing, and quit behavior should be reviewed when updating Codex.
