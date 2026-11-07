# Local Whisper dictation

This macOS helper keeps MLX Whisper loaded and writes partial transcripts into the active input line. The audio and transcription stay on the Mac.

Run `bash whisper-dictation/install.sh` from the repository root. It installs under `~/.local/share/whisper-dictation/` and reuses the `mlx-whisper` uv tool and cached `mlx-community/whisper-large-v3-turbo` model. The worker runs in offline mode.

Assign a keyboard key to F16 and configure Karabiner to open `~/.local/share/whisper-dictation/Whisper Dictation.app` in the background. The first launch loads Whisper. The next press starts recording; another press finishes the transcript and submits it with Enter. Escape cancels and erases the current recording's inserted text.

Recording inserts `🎙️  ` at the start of the current input line. Partial transcripts update after it about every three seconds, with timing depending on inference speed. Stopping completes transcription, removes the recording marker, moves to the end, and presses Enter. Use it in an empty chat prompt and leave the cursor in place while speaking. It sends Enter to the active app, so focus that chat before recording. If focus changes to another app, dictation stops and retains the latest result on the clipboard.

Allow microphone access and enable Whisper Dictation under System Settings → Privacy & Security → Device Control and Data Access (Accessibility on older macOS). Restart the helper after changing permissions so its Escape handler can connect. The menu-bar microphone turns red while recording and shows a waveform while finishing.

Vocabulary hints are read afresh for each transcription from `~/.config/whisper-dictation/vocabulary`, one term per line. `vocabulary-sources` can list additional vocabulary files, and `project-directories` can list directories whose child folder names are terminology. Both lists accept `~` paths. These private files stay outside the repository. Hints influence Whisper's spelling; they do not guarantee an exact match.

The worker log is `~/.local/share/whisper-dictation/worker.log`. Temporary raw audio is removed after finishing or cancellation. The transcript replaces the clipboard. The helper runs until quit from its menu; the first F16 press after login can launch it again.

The marker uses an explicit emoji presentation selector so the microphone occupies two terminal columns; its two following spaces occupy two more columns.
