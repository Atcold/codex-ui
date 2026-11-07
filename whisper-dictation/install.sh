#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
install_root="$HOME/.local/share/whisper-dictation"
app_bundle="$install_root/Whisper Dictation.app"
# Add speech detection to the existing Whisper environment without replacing its model.
uv pip install --python "$HOME/.local/share/uv/tools/mlx-whisper/bin/python" 'webrtcvad-wheels==2.0.14.post1'
mkdir -p "$app_bundle/Contents/MacOS"
swiftc main.swift -o "$app_bundle/Contents/MacOS/WhisperDictation" -framework AppKit -framework AVFoundation -framework ApplicationServices
cp worker.py "$install_root/worker.py"
cat > "$app_bundle/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>WhisperDictation</string>
<key>CFBundleIdentifier</key><string>local.whisper-dictation</string>
<key>CFBundleName</key><string>Whisper Dictation</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundleShortVersionString</key><string>0.1</string>
<key>LSUIElement</key><true/>
<key>NSMicrophoneUsageDescription</key><string>Record speech for local Whisper transcription.</string>
</dict></plist>
PLIST
codesign --force --sign - "$app_bundle"
printf 'Installed %s\n' "$app_bundle"
