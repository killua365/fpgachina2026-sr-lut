"""Reproduce Windows delete-sharing contention without accessing hardware."""
import ctypes
from ctypes import wintypes
import errno
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
from longrun_lease import refresh


class LeaseTests(unittest.TestCase):
    def test_transient_permission_error_retries_without_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            control = Path(tmp)
            target = control / 'lease.txt'
            target.write_text('123')
            original = Path.replace
            attempts = []
            def replace(path, destination):
                attempts.append(1)
                if len(attempts) <= 3:
                    self.assertEqual(target.read_text(), '123')
                    raise PermissionError('reader holds lease')
                return original(path, destination)
            with patch.object(Path, 'replace', replace):
                self.assertEqual(refresh(control, 456), 3)
            self.assertEqual(target.read_text(), '456')

    def test_persistent_denial_is_bounded_and_old_lease_remains(self):
        with tempfile.TemporaryDirectory() as tmp:
            control = Path(tmp);(control / 'lease.txt').write_text('123')
            now = [0.0]
            def sleep(seconds): now[0] += seconds
            with patch.object(Path, 'replace', side_effect=PermissionError('held')):
                with self.assertRaises(TimeoutError):
                    refresh(control, 456, clock=lambda: now[0], sleep=sleep)
            self.assertAlmostEqual(now[0], 2.0)
            self.assertEqual((control / 'lease.txt').read_text(), '123')

    def test_other_io_errors_fail_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, 'replace', side_effect=OSError(errno.ENOSPC, 'full')) as replace:
                with self.assertRaises(OSError) as error: refresh(Path(tmp), 456)
                self.assertEqual(error.exception.errno, errno.ENOSPC)
                self.assertEqual(replace.call_count, 1)
            with self.assertRaises(ValueError): refresh(Path(tmp), 1, timeout=3)

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics')
    def test_actual_windows_reader_denies_replace_then_releases(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
                                      ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        with tempfile.TemporaryDirectory() as tmp:
            control = Path(tmp);target = control / 'lease.txt';target.write_text('123')
            # Read + write sharing allowed; delete sharing intentionally absent.
            handle = kernel.CreateFileW(str(target), 0x80000000, 3, None, 3, 0, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            try:
                pending = control / 'lease.pending';pending.write_text('456')
                with self.assertRaises(OSError) as error: pending.replace(target)
                self.assertIn(error.exception.winerror, (5, 32, 33))
                thread = threading.Thread(target=lambda: (time.sleep(.15), kernel.CloseHandle(handle)))
                thread.start()
                try:
                    start = time.monotonic();retries = refresh(control, 456)
                    self.assertGreater(retries, 0)
                    self.assertLess(time.monotonic() - start, 2.0)
                    self.assertEqual(target.read_text(), '456')
                finally: thread.join()
            except BaseException:
                kernel.CloseHandle(handle)
                raise


if __name__ == '__main__': unittest.main(verbosity=2)
