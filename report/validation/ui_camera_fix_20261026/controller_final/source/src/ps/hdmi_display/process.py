"""Cancellable local subprocesses without a shell or blocking output pipes."""

from __future__ import annotations

import subprocess
import tempfile
import threading
import time


class ImportCancelled(RuntimeError):
    pass


def run_cancellable_steps(args, *, timeout: float, cancel: threading.Event | None = None):
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise ImportCancelled("Operation cancelled")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output, stderr=errors)
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if cancel.is_set():
                    raise ImportCancelled("Operation cancelled")
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(args, timeout)
                yield None
            if cancel.is_set():
                raise ImportCancelled("Operation cancelled")
            output.seek(0)
            errors.seek(max(0, errors.tell() - 8192))
            stdout, stderr = output.read(1024 * 1024 + 1), errors.read()
            if len(stdout) > 1024 * 1024:
                raise RuntimeError("Subprocess metadata output exceeds 1 MB")
            result = subprocess.CompletedProcess(args, process.returncode,
                                                 stdout.decode("utf-8", "replace"),
                                                 stderr.decode("utf-8", "replace"))
            result.check_returncode()
            return result
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def run_cancellable(args, *, timeout, cancel=None):
    steps = run_cancellable_steps(args, timeout=timeout, cancel=cancel)
    try:
        while True:
            try:
                next(steps)
            except StopIteration as finished:
                return finished.value
            time.sleep(0.05)
    finally:
        steps.close()
