import subprocess
import sys
import threading
import unittest

from hdmi_display.process import ImportCancelled, run_cancellable


class ProcessTests(unittest.TestCase):
    def test_cancel_terminates_running_process(self):
        cancel = threading.Event()
        timer = threading.Timer(0.15, cancel.set)
        timer.start()
        try:
            with self.assertRaises(ImportCancelled):
                run_cancellable([sys.executable, "-c", "import time; time.sleep(10)"], timeout=5, cancel=cancel)
        finally:
            timer.cancel()
            timer.join()

    def test_timeout_and_exit_status_propagate(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_cancellable([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
        with self.assertRaises(subprocess.CalledProcessError):
            run_cancellable([sys.executable, "-c", "raise SystemExit(3)"], timeout=5)


if __name__ == "__main__":
    unittest.main()
