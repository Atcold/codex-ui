"""Checks for the PTY transport boundary and conservative footer/quit handling."""
import unittest
from types import SimpleNamespace

import pyte

from wrapper import (ExitGuard, Footer, Metadata, Screen, TitleFilter, empty_prompt,
                     first_command)

VERSION = '0.167.0'
TITLE = b'\x1b]2;codex | example-model | project | main | Context 12% used | 5h 80% left | 0.167.0 | Ready\x07'
NATIVE = ('\x1b[10;1H› Ask Codex to do anything'
          '\x1b[12;1H  0.167.0\x1b[2;34m\x1b[10;3H').encode()


def fixture():
    metadata = Metadata()
    TitleFilter(metadata, VERSION).feed(TITLE)
    screen = Screen(100, 30)
    stream = pyte.ByteStream(screen)
    stream.feed(NATIVE)
    return metadata, screen, stream


class TransportTests(unittest.TestCase):
    def test_fragmentation_preserves_non_title_output(self):
        data = b'\x1b[2Jhello\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\' + TITLE + 'λ'.encode()
        baseline = TitleFilter(Metadata(), VERSION).feed(data)
        for boundary in range(len(data) + 1):
            metadata = Metadata()
            parser = TitleFilter(metadata, VERSION)
            self.assertEqual(parser.feed(data[:boundary]) + parser.feed(data[boundary:]), baseline)
            self.assertEqual(metadata.context, 12)
            self.assertEqual(metadata.used, 20)
        self.assertIn(b'\x1b[2Jhello\x1b]8;;https://example.com\x1b\\link', baseline)
        self.assertTrue(baseline.endswith('λ'.encode()))
        self.assertNotIn(b'Context 12%', baseline)

    def test_footer_changes_only_its_row_and_restores_cursor_and_attributes(self):
        metadata, source, _ = fixture()
        before = source.display[:]
        cursor = source.cursor.x, source.cursor.y, source.cursor.attrs, source.faint
        footer = Footer(metadata, 'machine', VERSION, SimpleNamespace(window={}))
        patch = footer.render(source, ExitGuard(), 100)
        self.assertEqual(source.display, before)
        receiver = Screen(100, 30)
        stream = pyte.ByteStream(receiver)
        stream.feed(NATIVE)
        stream.feed(patch)
        for row in range(30):
            if row != 11:
                self.assertEqual(receiver.display[row], before[row])
        self.assertEqual((receiver.cursor.x, receiver.cursor.y, receiver.cursor.attrs, receiver.faint), cursor)
        self.assertIn('example-model@machine project', receiver.display[11])
        self.assertIn('12% ctx', receiver.display[11])
        self.assertIn('20%', receiver.display[11])

    def test_empty_guard_does_not_capture_text_or_dialogs(self):
        metadata, screen, stream = fixture()
        self.assertTrue(empty_prompt(screen, 11, metadata))
        stream.feed(b'\x1b[10;3Htyped prompt\x1b[K')
        self.assertFalse(empty_prompt(screen, 11, metadata))
        stream.feed(b'\x1b[10;3HAsk Codex to do anything\x1b[K\x1b[10;3H')
        metadata.state = 'Working'
        self.assertFalse(empty_prompt(screen, 11, metadata))
        metadata.state = 'Ready'
        self.assertFalse(empty_prompt(screen, None, metadata))

    def test_ctrl_d_confirmation_expiry_and_intervening_input(self):
        guard = ExitGuard()
        guard.screen_empty = True
        self.assertEqual(guard.filter(b'\x04', 10), b'')
        self.assertEqual(guard.filter(b'\x04', 10.9), b'\x04')
        guard.screen_empty = True
        self.assertEqual(guard.filter(b'\x04', 12), b'')
        self.assertEqual(guard.filter(b'\x04', 14), b'')
        self.assertEqual(guard.filter(b'x\x04', 14.1), b'x\x04')
        self.assertFalse(guard.armed(14.1))

    def test_no_overlay_after_leaving_alternate_screen(self):
        metadata, screen, stream = fixture()
        footer = Footer(metadata, 'machine', VERSION, SimpleNamespace(window={}))
        stream.feed(b'\x1b[?1049l')
        self.assertEqual(footer.render(screen, ExitGuard(), 0), b'')
        stream.feed(b'\x1b[?1049h')
        self.assertTrue(footer.render(screen, ExitGuard(), 0))

    def test_command_detection_skips_option_values(self):
        self.assertEqual(first_command(['--sandbox', 'danger-full-access', '--ask-for-approval', 'on-request', 'resume']), 'resume')
        self.assertEqual(first_command(['-c', 'model="review"', 'exec']), 'exec')
        self.assertIsNone(first_command(['--', 'review']))


if __name__ == '__main__':
    unittest.main()
