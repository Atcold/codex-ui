#!/usr/bin/env python3
"""Render the familiar footer around an unmodified official Codex process."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import pty
import re
import selectors
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import tty

import pyte
from wcwidth import wcwidth

ESC = '\x1b'
RESET = ESC + '[0m'
META_ITEMS = ['app-name', 'model', 'project-name', 'git-branch', 'context-used',
              'five-hour-limit', 'codex-version', 'run-state']
PASS_THROUGH = {'exec', 'e', 'review', 'login', 'logout', 'mcp', 'plugin', 'app-server',
                'remote-control', 'app', 'completion', 'update', 'doctor', 'sandbox',
                'debug', 'apply', 'queue', 'archive', 'delete', 'migrate-rollouts',
                'migrate-nfs-db', 'unarchive', 'help', 'features'}
PLACEHOLDERS = {'', 'Ask Codex to do anything', 'Ask a follow-up question'}


def clean(text: str) -> str:
    return ''.join(c for c in text if ord(c) >= 32 and ord(c) != 127)


@dataclass
class Metadata:
    model: str = ''
    directory: str = ''
    branch: str = ''
    context: int | None = None
    used: int | None = None
    state: str = ''

    def accept(self, title: str, version: str) -> bool:
        parts = title.split(' | ')
        if len(parts) < 5 or parts[0] != 'codex' or parts[-2] != version:
            return False
        self.model, self.directory = parts[1:3]
        self.state = parts[-1]
        self.context = None
        self.used = None
        self.branch = ''
        for part in parts[3:-2]:
            if match := re.fullmatch(r'Context (\d+)% used', part):
                self.context = int(match[1])
            elif match := re.fullmatch(r'(?:5h|primary|usage) (\d+)% left', part):
                self.used = max(0, min(100, 100 - int(match[1])))
            else:
                self.branch = part
        return True


class TitleFilter:
    """Buffer only OSC sequences; arbitrary split boundaries preserve other bytes."""
    def __init__(self, metadata: Metadata, version: str):
        self.metadata, self.version = metadata, version
        self.pending = bytearray()
        self.state = 'plain'

    def feed(self, data: bytes) -> bytes:
        output = bytearray()
        for byte in data:
            if self.state == 'plain':
                if byte == 27:
                    self.pending.append(byte)
                    self.state = 'escape'
                else:
                    output.append(byte)
            elif self.state == 'escape':
                self.pending.append(byte)
                if byte == 93:
                    self.state = 'osc'
                else:
                    output.extend(self.pending)
                    self.pending.clear()
                    self.state = 'plain'
            else:
                self.pending.append(byte)
                complete = byte == 7 or (self.state == 'osc-escape' and byte == 92)
                if complete:
                    packet = bytes(self.pending)
                    terminator = 2 if packet.endswith(b'\x1b\\') else 1
                    payload = packet[2:-terminator].decode('utf-8', 'replace')
                    code, separator, title = payload.partition(';')
                    if code in {'0', '2'} and separator and self.metadata.accept(title, self.version):
                        # Keep private metadata out of the visible terminal title.
                        visible = clean(f'codex | {self.metadata.directory} | {self.metadata.state}')
                        output.extend(f'{ESC}]2;{visible}\a'.encode())
                    else:
                        output.extend(packet)
                    self.pending.clear()
                    self.state = 'plain'
                elif len(self.pending) > 1048576:
                    output.extend(self.pending)
                    self.pending.clear()
                    self.state = 'plain'
                else:
                    self.state = 'osc-escape' if byte == 27 else 'osc'
        return bytes(output)


class Screen(pyte.Screen):
    """Track current faint SGR as well as pyte's standard attributes."""
    def __init__(self, columns: int, lines: int):
        self.faint = False
        self.suspended = False
        super().__init__(columns, lines)

    def set_mode(self, *modes, **kwargs):
        if kwargs.get('private') and 1049 in modes:
            self.suspended = False
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get('private') and 1049 in modes:
            self.suspended = True
        super().reset_mode(*modes, **kwargs)

    def select_graphic_rendition(self, *attrs: int) -> None:
        values = list(attrs or (0,))
        index = 0
        while index < len(values):
            value = values[index]
            if value in {0, 22}:
                self.faint = False
            elif value == 2:
                self.faint = True
            elif value in {38, 48} and index + 1 < len(values):
                mode = values[index + 1]
                index += 4 if mode == 2 else 2
            index += 1
        super().select_graphic_rendition(*attrs)


def restore_sgr(screen: Screen) -> str:
    attrs = screen.cursor.attrs
    codes = ['0']
    for field, number in [('bold', 1), ('italics', 3), ('underscore', 4),
                          ('blink', 5), ('reverse', 7), ('strikethrough', 9)]:
        if getattr(attrs, field):
            codes.append(str(number))
    if screen.faint:
        codes.append('2')
    names = ['black', 'red', 'green', 'brown', 'blue', 'magenta', 'cyan', 'white']
    for field, base in [('fg', 30), ('bg', 40)]:
        color = getattr(attrs, field)
        if color in names:
            codes.append(str(base + names.index(color)))
        elif color.startswith('bright') and color[6:] in names:
            codes.append(str(base + 60 + names.index(color[6:])))
        elif re.fullmatch('[0-9a-fA-F]{6}', color):
            codes += [str(base + 8), '2', *(str(int(color[i:i + 2], 16)) for i in (0, 2, 4))]
    return ESC + '[' + ';'.join(codes) + 'm'


def fit_segments(segments: list[tuple[str, str]], width: int) -> str:
    output, used = [], 0
    for text, style in segments:
        chars = []
        for char in clean(text):
            size = max(0, wcwidth(char))
            if used + size > width:
                break
            chars.append(char)
            used += size
        if chars:
            output += [ESC + '[0;' + style + 'm', ''.join(chars)]
        if used >= width:
            break
    return ''.join(output) + RESET


class ExitGuard:
    def __init__(self):
        self.armed_until = 0.0
        self.screen_empty = False

    def filter(self, data: bytes, now: float) -> bytes:
        result = bytearray()
        for byte in data:
            if byte == 4 and self.screen_empty:
                if self.armed_until and now <= self.armed_until:
                    self.armed_until = 0
                    result.append(byte)
                else:
                    self.armed_until = now + 1.0
            else:
                self.armed_until = 0
                self.screen_empty = False
                result.append(byte)
        return bytes(result)

    def armed(self, now: float) -> bool:
        return now < self.armed_until


def empty_prompt(screen: Screen, footer_row: int | None, metadata: Metadata) -> bool:
    if footer_row is None or metadata.state != 'Ready' or screen.cursor.hidden:
        return False
    row = screen.cursor.y
    if row >= footer_row or footer_row - row > 8:
        return False
    line = screen.display[row]
    match = re.match(r'^\s*› ', line)
    return bool(match and line[match.end():].strip() in PLACEHOLDERS
                and screen.cursor.x == match.end())


class RateReader:
    """Use the signed client's read-only usage RPC; never read or print tokens."""
    def __init__(self, binary: str):
        self.binary = binary
        self.snapshot: dict = {}
        self.stop = threading.Event()
        self.process: subprocess.Popen | None = None

    def window_for(self, minutes: int) -> dict:
        for name in ('primary', 'secondary'):
            window = self.snapshot.get(name) or {}
            if window.get('windowDurationMins') == minutes:
                return window
        return {}

    @property
    def window(self) -> dict:
        return self.window_for(300)

    @property
    def weekly(self) -> dict:
        return self.window_for(10080)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self.stop.is_set():
            try:
                self.snapshot = self.read()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                pass  # Native percentages still work if reset time is unavailable.
            self.stop.wait(90)

    def read(self) -> dict:
        process = subprocess.Popen([self.binary, 'app-server', '--stdio'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.process = process
        buffer = bytearray()
        try:
            assert process.stdin is not None and process.stdout is not None
            def send(message):
                process.stdin.write((json.dumps(message) + '\n').encode())
                process.stdin.flush()
            def response(identifier):
                deadline = time.monotonic() + 15
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while not self.stop.is_set() and time.monotonic() < deadline:
                        while b'\n' in buffer:
                            line, _, remaining = buffer.partition(b'\n')
                            buffer[:] = remaining
                            message = json.loads(line)
                            if message.get('id') == identifier:
                                if 'error' in message:
                                    raise RuntimeError('Usage RPC unavailable')
                                return message['result']
                        if not selector.select(0.2):
                            continue
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            raise RuntimeError('Usage RPC closed')
                        buffer.extend(chunk)
                raise RuntimeError('Usage RPC timed out')
            send({'id': 1, 'method': 'initialize', 'params': {
                'clientInfo': {'name': 'codex-ui-footer', 'version': '1.0.0'}}})
            response(1)
            send({'method': 'initialized'})
            send({'id': 2, 'method': 'account/rateLimits/read'})
            result = response(2)
            snapshot = (result.get('rateLimitsByLimitId') or {}).get('codex', result.get('rateLimits', {}))
            return snapshot
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            self.process = None

    def close(self):
        self.stop.set()
        if self.process is not None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quota_segments(used: float, reset, duration: int, now: float,
                   *, weekly: bool = False) -> list[tuple[str, str]]:
    filled = min(10, max(0, math.floor(used / 10 + 0.5)))
    color = '31' if filled >= 9 else '38;5;208' if filled >= 7 else '33' if filled >= 4 else '32'
    segments = [(f' · {math.floor(used + 0.5)}% ', '2')]
    marker = -1
    pace_color = '38;5;255'
    if is_number(reset):
        remaining = min(duration, max(0, int(reset) - int(now)))
        elapsed_pct = (duration - remaining) * 100 // duration
        marker = (elapsed_pct * 10 + 50) // 100
        # Match Claude's pace colours; the first tenth of each window stays neutral.
        if 10 <= elapsed_pct < 100:
            projected = used * 100 // elapsed_pct
            for ceiling, palette in [(50, 34), (75, 37), (90, 178),
                                     (100, 166), (120, 160), (math.inf, 129)]:
                if projected < ceiling:
                    pace_color = f'38;5;{palette}'
                    break
    for boundary in range(11):
        if boundary == marker:
            segments += [('│', pace_color)]
        if boundary < 10:
            segments += [('◼' if boundary < filled else '◻', '2;' + color)]
    if is_number(reset) and reset > now:
        local = datetime.fromtimestamp(reset)
        weekday = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[local.weekday()]
        day = f'{weekday} ' if weekly else ''
        segments += [(f' → {day}{local.hour}:{local.minute:02}', '2')]
    return segments


class Footer:
    def __init__(self, metadata: Metadata, host: str, version: str, rates: RateReader):
        self.metadata, self.host, self.version, self.rates = metadata, host, version, rates
        self.last = None

    def locate(self, screen: Screen) -> int | None:
        if not self.metadata.model or screen.suspended:
            return None
        lines = screen.display
        for row in range(screen.lines - 1, -1, -1):
            if lines[row].strip() == self.version and any(
                    re.match(r'^\s*› ', lines[previous])
                    for previous in range(max(0, row - 8), row)):
                return row
        return None

    def render(self, screen: Screen, guard: ExitGuard, now: float) -> bytes:
        row = self.locate(screen)
        guard.screen_empty = empty_prompt(screen, row, self.metadata)
        if row is None or screen.cursor.x >= screen.columns:
            self.last = None
            return b''
        metadata = self.metadata
        segments = [(metadata.model, '1;35'), ('@', '1;37'), (self.host, '1;34'),
                    (' ' + metadata.directory, '1;33')]
        if metadata.branch:
            segments += [(' · ' + metadata.branch, '2;32')]
        if metadata.context is not None:
            segments += [(f' · {metadata.context}% ctx', '2')]
        wall_now = time.time()
        if metadata.used is not None:
            segments += quota_segments(metadata.used, self.rates.window.get('resetsAt'),
                                       18000, wall_now)
        weekly = getattr(self.rates, 'weekly', {})
        used, reset = weekly.get('usedPercent'), weekly.get('resetsAt')
        if (is_number(used) and 0 <= used <= 100 and is_number(reset)
                and reset > wall_now):
            elapsed = min(604800, max(0, 604800 - (reset - wall_now)))
            if used * 604800 > elapsed * 100:
                segments += quota_segments(used, reset, 604800, wall_now, weekly=True)
        if guard.armed(now):
            segments += [(' · Ctrl-D again to exit', '33')]
        content = fit_segments(segments, max(0, screen.columns - 3))
        state = (row, screen.columns, content)
        if self.last == state and row not in screen.dirty:
            return b''
        self.last = state
        # Explicit coordinates keep Codex's own saved-cursor slot untouched.
        patch = (f'{ESC}[{row + 1};1H{ESC}[2K  {content}'
                 f'{ESC}[{screen.cursor.y + 1};{screen.cursor.x + 1}H{restore_sgr(screen)}')
        return patch.encode()


def host_name() -> str:
    try:
        return subprocess.check_output(['scutil', '--get', 'ComputerName'], text=True,
                                       stderr=subprocess.DEVNULL, timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        return socket.gethostname().split('.')[0]


def terminal_size(fd: int) -> tuple[int, int, bytes]:
    packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, b'\0' * 8)
    rows, columns, _, _ = struct.unpack('HHHH', packed)
    return max(1, rows), max(1, columns), packed


def write_all(fd: int, data: bytes):
    while data:
        count = os.write(fd, data)
        data = data[count:]


def first_command(args: list[str]) -> str | None:
    takes_value = {'-c', '--config', '-C', '--cd', '-m', '--model', '-p', '--profile',
                   '-s', '--sandbox', '-a', '--ask-for-approval', '--add-dir',
                   '--enable', '--disable', '--image', '-i'}
    skip = False
    for value in args:
        if skip:
            skip = False
            continue
        if value == '--':
            return None
        if value in takes_value:
            skip = True
        elif not value.startswith('-'):
            return value
    return None


def main() -> int:
    binary = os.environ.get('CODEX_UI_OFFICIAL_BINARY') or shutil.which('codex')
    if not binary:
        raise SystemExit('Install the official Codex CLI first.')
    binary = str(Path(binary).resolve())
    args = sys.argv[1:]
    if (not sys.stdin.isatty() or not sys.stdout.isatty()
            or any(a in {'--help', '-h', '--version', '-V'} for a in args)
            or first_command(args) in PASS_THROUGH):
        os.execv(binary, [binary, *args])
    version_output = subprocess.check_output([binary, '--version'], text=True)
    version = version_output.strip().split()[-1]
    metadata = Metadata()
    titles = TitleFilter(metadata, version)
    rows, columns, size = terminal_size(0)
    screen = Screen(columns, rows)
    stream = pyte.ByteStream(screen)
    rates = RateReader(binary)
    footer = Footer(metadata, host_name(), version, rates)
    guard = ExitGuard()
    settings = ['-c', 'tui.status_line=["codex-version"]',
                '-c', 'tui.terminal_title=' + json.dumps(META_ITEMS)]
    saved_tty = termios.tcgetattr(0)
    child, master = pty.fork()
    if child == 0:
        os.execv(binary, [binary, *settings, *args])
    fcntl.ioctl(master, termios.TIOCSWINSZ, size)
    resize_pending = False
    def resized(_signum, _frame):
        nonlocal resize_pending
        resize_pending = True
    previous_handler = signal.signal(signal.SIGWINCH, resized)
    rates.start()
    try:
        tty.setraw(0)
        with selectors.DefaultSelector() as selector:
            selector.register(0, selectors.EVENT_READ)
            selector.register(master, selectors.EVENT_READ)
            while True:
                if resize_pending:
                    resize_pending = False
                    rows, columns, size = terminal_size(0)
                    fcntl.ioctl(master, termios.TIOCSWINSZ, size)
                    screen.resize(lines=rows, columns=columns)
                    footer.last = None
                for key, _ in selector.select(0.1):
                    if key.fd == master:
                        try:
                            data = os.read(master, 65536)
                        except OSError as error:
                            if error.errno == errno.EIO:
                                data = b''
                            else:
                                raise
                        if not data:
                            _, status = os.waitpid(child, 0)
                            return os.waitstatus_to_exitcode(status)
                        write_all(1, titles.feed(data))
                        stream.feed(data)
                    else:
                        data = os.read(0, 65536)
                        if not data:
                            return 0
                        filtered = guard.filter(data, time.monotonic())
                        if filtered:
                            write_all(master, filtered)
                patch = footer.render(screen, guard, time.monotonic())
                if patch:
                    write_all(1, patch)
                screen.dirty.clear()
    finally:
        rates.close()
        signal.signal(signal.SIGWINCH, previous_handler)
        termios.tcsetattr(0, termios.TCSADRAIN, saved_tty)
        os.close(master)


if __name__ == '__main__':
    raise SystemExit(main())
