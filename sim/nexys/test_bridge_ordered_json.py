import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'board/host'))
from bridge_ordered_json import write


class JsonPublicationTests(unittest.TestCase):
    def test_non_sharing_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'status.json'
            write(target, {'old': True})
            with patch.object(Path, 'replace', side_effect=OSError('disk fault')) as replace:
                with self.assertRaisesRegex(OSError, 'disk fault'):
                    write(target, {'new': True})
            self.assertEqual(replace.call_count, 1)
            self.assertEqual(json.loads(target.read_text()), {'old': True})

    def test_budget_and_serialization_failures_keep_old_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'status.json'
            write(target, {'old': True})
            for budget in (0, -1, 2.01):
                with self.assertRaises(ValueError):
                    write(target, {}, timeout=budget)
            with self.assertRaises(TypeError):
                write(target, {'bad': object()})
            self.assertEqual(json.loads(target.read_text()), {'old': True})

    @unittest.skipUnless(os.name == 'nt', 'Requires real Windows file sharing')
    def test_actual_transient_reader_lock_retries_without_truncation(self):
        self.windows_lock_case(persistent=False)

    @unittest.skipUnless(os.name == 'nt', 'Requires real Windows file sharing')
    def test_actual_persistent_reader_lock_fails_bounded_and_preserves_old(self):
        self.windows_lock_case(persistent=True)

    def windows_lock_case(self, persistent):
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'status.json'
            write(target, {'old': True})
            # Read/write sharing, deliberately no FILE_SHARE_DELETE, like the
            # PowerShell reader involved in the 2026-10-09 failed board attempt.
            handle = create(str(target), 0x80000000, 3, None, 3, 0x80, None)
            self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
            timer = None
            try:
                if not persistent:
                    timer = threading.Timer(.2, lambda: close(handle))
                    timer.start()
                start = time.monotonic()
                if persistent:
                    with self.assertRaises(TimeoutError):
                        write(target, {'new': True}, timeout=.15)
                    self.assertEqual(json.loads(target.read_text()), {'old': True})
                    self.assertEqual(json.loads(target.with_suffix('.json.pending').read_text()), {'new': True})
                else:
                    write(target, {'new': True})
                    self.assertEqual(json.loads(target.read_text()), {'new': True})
                    self.assertFalse(target.with_suffix('.json.pending').exists())
                elapsed = time.monotonic() - start
                self.assertGreaterEqual(elapsed, .12)
                self.assertLess(elapsed, 2.0)
            finally:
                if timer is not None:
                    timer.join()
                else:
                    close(handle)
            if persistent:
                write(target, {'recovered': True})
                self.assertEqual(json.loads(target.read_text()), {'recovered': True})


if __name__ == '__main__':
    unittest.main()
