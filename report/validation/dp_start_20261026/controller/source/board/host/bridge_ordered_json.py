"""Atomic JSON publication with a bounded Windows sharing-conflict retry.

Keep the previous complete document visible until replacement succeeds. A
persistent permission/sharing error is still a controller failure, never a pass.
"""
import json
import time


def write(path, value, *, timeout=2.0, clock=time.monotonic, sleep=time.sleep):
    if not 0 < timeout <= 2.0:
        raise ValueError('JSON replacement retry budget must be within two seconds')
    content = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(content, encoding='utf-8')
    deadline = clock() + timeout
    while True:
        try:
            pending.replace(path)
            return
        except OSError as error:
            if not isinstance(error, PermissionError) and getattr(error, 'winerror', None) not in (5, 32, 33):
                raise
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError('JSON replacement remained unavailable within retry budget: ' + str(path)) from error
            sleep(min(.025, remaining))
