"""Bounded atomic lease replacement when a Windows reader temporarily holds it.

Never truncate the live lease. A persistent sharing/permission error is still a
supervisor fault and must enter the existing stop-and-recover path.
"""
import time


def refresh(control, value, *, timeout=2.0, clock=time.monotonic, sleep=time.sleep):
    if not 0 < timeout <= 2.0:
        raise ValueError('Lease retry budget must be within two seconds')
    pending = control / 'lease.pending'
    target = control / 'lease.txt'
    pending.write_text(str(int(value)), encoding='ascii')
    deadline = clock() + timeout
    retries = 0
    while True:
        try:
            pending.replace(target)
            return retries
        except OSError as error:
            if not isinstance(error, PermissionError) and getattr(error, 'winerror', None) not in (5, 32, 33):
                raise
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError('Lease replacement remained unavailable within retry budget') from error
            retries += 1
            sleep(min(.025, remaining))
