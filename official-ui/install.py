#!/usr/bin/env python3
"""Install the official-client wrapper without changing the official executable."""
from pathlib import Path
import os
import shutil
import subprocess

source = Path(__file__).resolve().parent
root = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'codex-ui'
wrapper = root / 'official-ui'
python = wrapper / 'venv/bin/python'
wrapper.mkdir(parents=True, exist_ok=True)
if not python.exists():
    subprocess.run(['uv', 'venv', str(wrapper / 'venv')], check=True)
subprocess.run(['uv', 'pip', 'install', '--python', str(python), 'pyte==0.8.2'], check=True)
shutil.copy2(source / 'wrapper.py', wrapper / 'wrapper.py')
launcher = root / 'codex-ui'
backup = root / 'codex-ui-patched'
if launcher.exists() and not backup.exists():
    shutil.copy2(launcher, backup)
launcher.write_text('#!/bin/sh\nscript_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                    'exec "$script_dir/official-ui/venv/bin/python" "$script_dir/official-ui/wrapper.py" "$@"\n')
launcher.chmod(0o755)
command = Path.home() / '.local/bin/codex-ui'
command.parent.mkdir(parents=True, exist_ok=True)
if not command.exists():
    command.symlink_to(launcher)
print('Installed official Codex wrapper. Restart codex-ui to use it.')
print('The prior launcher is retained as codex-ui-patched beside it.')
