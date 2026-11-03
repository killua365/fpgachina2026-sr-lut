"""Monotonic N4 operator experiment. Limits do not imply stage acceptance."""
class Monitor:
    def __init__(self, now):
        self.started = self.previous = now
        self.active = self.programming = self.sample = self.finish = self.stop = self.reason = None
        self.requested = False

    def tick(self, now, *, active=False, programming=False, sample_ack=False,
             loader_done=False, capture_ok=True, cancel=False, finish_requested=False):
        if now < self.previous: raise ValueError('Monotonic timer reversed')
        self.previous = now
        if programming and self.programming is None: self.programming = now
        if active and self.active is None: self.active = now
        actions = []
        if sample_ack and self.sample is None:
            if not self.requested: raise ValueError('Unrequested acknowledgement')
            self.sample = now; actions.append('sample_ack')
        if loader_done:
            if self.finish is None and self.reason is None: self.reason = 'loader_ended_early'
            return ['done']
        reason = ('user_cancel' if cancel else
                  'capture_failed_or_stale' if not capture_ok else
                  'total_deadline' if now-self.started >= 780 else
                  'configuration_hold_limit' if self.programming is not None and now-self.programming >= 600 else
                  'load_timeout' if self.active is None and now-self.started >= 180 else
                  'sample_start_timeout' if self.requested and self.sample is None and now-self.active >= 90 else
                  'experiment_window_timeout' if self.sample is not None and self.finish is None and now-self.sample >= 480 else
                  'loader_stalled_after_finish' if self.finish is not None and now-self.finish >= 60 else None)
        if reason and self.stop is None:
            self.reason = reason; self.stop = now; actions.append('stop')
        if self.stop is not None:
            if now-self.stop >= 60: actions.append('terminate')
            return actions
        if self.active is not None and now-self.active >= 30 and not self.requested:
            self.requested = True; actions.append('sample_start')
        if finish_requested and self.finish is None:
            if self.sample is None: raise ValueError('Finish before experiment window')
            self.finish = now; actions.append('finish')
        return actions

    def summary(self):
        return dict(warmup_seconds=30, reason=self.reason, active_qpc_seconds=self.active,
                    sample_start_qpc_seconds=self.sample, sample_finish_request_qpc_seconds=self.finish,
                    sample_seconds=None if self.finish is None else self.finish-self.sample,
                    experiment_complete=self.finish is not None and self.reason is None,
                    nominal_performance_passed=False)
