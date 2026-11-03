"""N5 bounded business session: close measurement, stop its source, restore QSPI.

Pure policy; all times are monotonic seconds. No hardware or process access.
The independent loader has a 1260-second hold limit. Normal worst case is
180 load + 30 warmup + 5 start + 720 sample + 5 close + 300 stop = 1240 s,
leaving 20 s before that independent cap. Recovery has its own 120 s bound.
Operator sessions use a 2400-second sampling backstop and 2940-second loader
cap; they normally end on an explicit close. Fault/stop/recovery limits stay
unchanged. The source separately enforces 1800 interactive seconds.
"""
import math

LIMITS = dict(load=180, warmup=30, acknowledgement=5, window=720,
              source_stop=300, recovery=120, hold=1260, total=1380)
OPERATOR_LIMITS = dict(LIMITS, window=2400, hold=2940, total=3060)
DEMO_LIMITS = dict(OPERATOR_LIMITS, window=None, hold=None, total=None)


def limits_for(end_mode='timed'):
    if end_mode not in ('timed', 'operator', 'demo'):
        raise ValueError('Unknown session end mode')
    return dict(DEMO_LIMITS if end_mode == 'demo' else OPERATOR_LIMITS if end_mode == 'operator' else LIMITS)


class Monitor:
    def __init__(self, now, case, end_mode='timed'):
        if case not in ('business',):
            raise ValueError('Unknown case')
        self.case = case
        self.limits = limits_for(end_mode)
        self.end_mode = end_mode
        self.started = self.previous = now
        self.state = 'loading'
        self.active = self.sample = self.close_request = self.closed = None
        self.start_request = self.source_request = self.restore = None
        self.failures = []
        self.formal_failures = None

    def fail(self, reason):
        if reason not in self.failures:
            self.failures.append(reason)

    def tick(self, now, *, active=False, start_ack=False, close_ack=False,
             source_verified=False, input_absent=False, loader_done=False,
             capture_ok=True, cancel=False, close=False, fault=None):
        if not math.isfinite(now) or now < self.previous:
            raise ValueError('Invalid monotonic clock')
        self.previous = now
        actions = []
        if active and self.active is None:
            self.active = now
            if self.state == 'loading': self.state = 'warming'
        if fault: self.fail(fault)
        if cancel: self.fail('user_cancel')
        if not capture_ok: self.fail('capture_failed_or_stale')
        if self.limits['total'] is not None and now-self.started >= self.limits['total']: self.fail('total_deadline')
        if self.active is None and now-self.started >= self.limits['load']:
            self.fail('load_timeout')
        if start_ack and self.sample is None:
            if self.start_request is None or self.state != 'starting':
                self.fail('unexpected_start_ack')
            elif now-self.start_request > self.limits['acknowledgement']:
                self.fail('late_start_ack')
            else:
                self.sample = now; self.state = 'sampling'; actions.append('sample_started')
        if close_ack and self.closed is None:
            if self.close_request is None or self.state != 'closing':
                self.fail('unexpected_close_ack')
            elif now-self.close_request > self.limits['acknowledgement']:
                self.fail('late_close_ack')
            else:
                self.closed = now; self.state = 'stopping_source'; actions.append('sample_closed')
                self.formal_failures = list(self.failures)
        if self.state == 'starting' and now-self.start_request > self.limits['acknowledgement']:
            self.fail('start_ack_timeout')
        if close and self.state not in ('sampling', 'closing', 'stopping_source', 'restoring'):
            self.fail('close_before_sampling')
        if self.state == 'sampling' and self.limits['window'] is not None and now-self.sample >= self.limits['window']:
            self.fail('window_timeout')
        if loader_done and self.state != 'restoring':
            self.fail('loader_ended_early')
        if self.state in ('loading', 'warming', 'starting', 'sampling'):
            if self.failures or close:
                self.close_request = now; self.state = 'closing'; actions.append('close_sample')
            elif self.state == 'warming' and now-self.active >= self.limits['warmup']:
                self.start_request = now; self.state = 'starting'; actions.append('start_sample')
        if self.state == 'closing' and now-self.close_request > self.limits['acknowledgement']:
            self.fail('close_ack_timeout')
            # Still try owned source cancellation, but no valid sample boundary.
            self.state = 'stopping_source'
        if self.state == 'stopping_source':
            if self.source_request is None:
                self.source_request = now; actions.append('stop_source')
            timed_out = now-self.source_request >= self.limits['source_stop']
            if timed_out: self.fail('source_stop_timeout')
            if source_verified and input_absent or timed_out:
                if not (source_verified and input_absent): self.fail('source_order_unverified')
                self.restore = now; self.state = 'restoring'; actions.append('restore')
        if self.state == 'restoring':
            if loader_done:
                self.state = 'done'; actions.append('done')
            elif now-self.restore >= self.limits['recovery']:
                self.fail('loader_recovery_timeout'); self.state = 'done'; actions.append('terminate')
        return actions

    def summary(self):
        duration = None if self.sample is None or self.closed is None else self.closed-self.sample
        return dict(state=self.state, end_mode=self.end_mode, failures=list(self.failures), active=self.active,
                    start_request=self.start_request, sample_start=self.sample,
                    close_request=self.close_request, sample_close=self.closed,
                    source_request=self.source_request, restore_request=self.restore,
                    sample_seconds=duration, formal_failures=self.formal_failures,
                    measurement_complete=(duration is not None and
                    duration >= 40 and self.formal_failures==[]),
                    n5_passed=False, n6_passed=False)
