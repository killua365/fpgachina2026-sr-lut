"""Operator interaction must outlive old limits without losing fault recovery."""
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import sys
import unittest
from unittest.mock import patch

from hdmi_business_app import InteractionBudget
import hdmi_business_app as app
from business_session_policy import Monitor, LIMITS, OPERATOR_LIMITS
from business_session_remote import validate_launch, verify_worker, verify_final, worker_command
from test_business_session import fixture
from prepare_business_session import prepare
import prepare_business_session as preparation


class OperatorTests(unittest.TestCase):
    def test_cli_propagates_manual_mode_and_rejects_short_cap(self):
        args = ['app', 'run', '--root', str(Path(__file__).resolve().parents[3]), '--seconds', '1800',
                '--end-mode', 'operator', '--run-id', 'a'*32,
                '--destination', '/home/xilinx/n5_business_results_'+'a'*32]
        with patch.object(app, 'supervise') as launch, patch.object(app.faulthandler, 'enable'):
            with patch.object(sys, 'argv', args):
                app.main()
            self.assertEqual(launch.call_args.kwargs['end_mode'], 'operator')
            launch.reset_mock()
            with patch.object(sys, 'argv', args+['--seconds', '300']), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    app.main()
            launch.assert_not_called()

    def test_default_launch_is_demo_but_explicit_seconds_retains_timed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(preparation, 'ROOT', Path(tmp)):
            manual = json.loads(prepare().read_text())
            timed = json.loads(prepare(60).read_text())
        self.assertEqual((manual['end_mode'], manual['seconds'], manual['supervisor_deadline_seconds']),
                         ('demo', None, None))
        self.assertEqual((timed['end_mode'], timed['seconds']), ('timed', 60))

    def test_operator_outlives_five_minutes_and_ends_on_operator(self):
        now = [10.]
        budget = InteractionBudget(1800, 'operator', lambda: now[0])
        cancel = threading.Event()
        now[0] += 301
        self.assertTrue(budget.active(cancel))
        self.assertIsNone(budget.display_deadline)
        cancel.set()
        self.assertFalse(budget.active(cancel))
        report = {'errors': []}
        budget.finish(cancel, report)
        self.assertEqual(report, {'interactive_end': 'operator', 'errors': []})

    def test_safety_cap_recovers_as_failure_not_success(self):
        now = [0.]
        budget = InteractionBudget(1800, 'operator', lambda: now[0])
        now[0] = 1800
        cancel = threading.Event()
        self.assertFalse(budget.active(cancel))
        report = {'errors': []}
        budget.finish(cancel, report)
        self.assertEqual(report['interactive_end'], 'safety_timeout')
        self.assertTrue(report['errors'])
        timed = InteractionBudget(60, 'timed', lambda: 0.)
        self.assertEqual(timed.display_deadline, 60)
        timed.finish(cancel, report := {'errors': []})
        self.assertEqual(report, {'interactive_end': 'duration_complete', 'errors': []})

    def test_mode_bound_to_launch_process_worker_and_supervisor(self):
        contract, launch, final = fixture()
        launch.update(end_mode='operator', seconds=1800, supervisor_deadline_seconds=2040)
        self.assertEqual(validate_launch(launch, contract), launch)
        self.assertEqual(worker_command(launch, 7)[-2:], ['--end-mode', 'operator'])
        for group in ('worker', 'supervisor'):
            final[group].update(end_mode='operator', requested_seconds=1800)
        self.assertTrue(verify_final(final, launch, final['binding']))
        for group in ('worker', 'supervisor'):
            broken = copy.deepcopy(final)
            broken[group]['end_mode'] = 'timed'
            with self.assertRaises(ValueError):
                verify_final(broken, launch, broken['binding'])
        for change in ({'seconds': 300}, {'seconds': 1801}, {'end_mode': 'unlimited'}):
            with self.assertRaises(ValueError):
                validate_launch(dict(launch, **change), contract)

    def sampling(self):
        monitor = Monitor(0., 'business', 'operator')
        monitor.tick(1., active=True)
        monitor.tick(31.)
        monitor.tick(32., start_ack=True)
        return monitor

    def test_operator_controller_outlives_old_window_and_preserves_stop_order(self):
        monitor = self.sampling()
        self.assertEqual(monitor.tick(1400.), [])
        self.assertEqual(monitor.state, 'sampling')
        self.assertEqual(monitor.tick(1401., close=True), ['close_sample'])
        self.assertEqual(monitor.tick(1402., close_ack=True), ['sample_closed', 'stop_source'])
        self.assertEqual(monitor.tick(1403., source_verified=True), [])
        self.assertEqual(monitor.tick(1404., source_verified=True, input_absent=True), ['restore'])
        self.assertEqual(monitor.failures, [])

    def test_operator_faults_and_safety_caps_remain_enforced(self):
        for change, reason in (({'fault': 'source_probe_failed'}, 'source_probe_failed'),
                               ({'capture_ok': False}, 'capture_failed_or_stale')):
            monitor = self.sampling()
            self.assertEqual(monitor.tick(800., **change), ['close_sample'])
            self.assertIn(reason, monitor.failures)
        monitor = self.sampling()
        self.assertEqual(monitor.tick(2432.), ['close_sample'])
        self.assertIn('window_timeout', monitor.failures)
        monitor.tick(2433., close_ack=True)
        self.assertEqual(monitor.tick(2733.), ['restore'])
        self.assertIn('source_stop_timeout', monitor.failures)
        for limits in (LIMITS, OPERATOR_LIMITS):
            self.assertLess(sum(limits[k] for k in ('load', 'warmup', 'window', 'source_stop')) +
                            2*limits['acknowledgement'], limits['hold'])
            self.assertEqual(limits['total'], limits['hold']+limits['recovery'])
