"""Run the actual N5 controller with clock/transport/process fault injection."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'board/host'))
import supervise_business_session as s
import business_session_gate as gate
from test_bridge_continuous_supervisor import World, Immediate
from bridge_ordered_protocol import send


class BusinessWorld(World):
    def live(self, raw):
        value = super().live(raw)
        if self.scenario == 'pipeline_error' and self.now > 80:
            value['error_flags'] = 1
        return value

    def query(self, action='inspect'):
        if not (self.out/'events.jsonl').exists():
            return dict(state='absent')
        if self.scenario == 'source_ssh_failed' and self.now > 70:
            raise RuntimeError('SSH failed')
        if self.scenario == 'operator_safety' and self.now > 1400:
            self.stopped = True
            return dict(state='finished', worker={'errors': ['Operator session safety cap reached']})
        if self.scenario in ('slow_parent', 'stuck_parent') and self.now > 200:
            if self.scenario == 'slow_parent' and self.now > 275:
                self.stopped = True
                return dict(state='finished')
            return dict(state='finalizing')
        if self.scenario in ('natural', 'factory_uart') and self.now > 200:
            self.stopped = True
        elif self.scenario not in ('worker_stuck', 'natural', 'factory_uart', 'slow_parent', 'stuck_parent'):
            close_at = 1400 if self.scenario in ('operator_long', 'operator_safety') else 140
            if self.now > close_at and not (self.out/'close.request').exists():
                send(self.out/'close.request', 'a'*32, 'close')
            events = self.out/'events.jsonl'
            if self.stop_at is None and events.exists() and 'operator_source_stop_prompt' in events.read_text():
                self.stop_at = self.now
            if self.stop_at is not None and self.now-self.stop_at > 4:
                self.stopped = True
        return dict(state='finished' if self.stopped else 'running', remaining_seconds=540-self.now)


class ControllerTests(unittest.TestCase):
    def run_case(self, scenario, end_mode='timed'):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as name:
            root = Path(name).resolve()
            out, package = root/'board/acceptance/run', root/'package'
            package.mkdir()
            (package/'manifest.json').write_text('{}')
            world = BusinessWorld(out, scenario)
            with patch.object(gate, 'check'), patch.object(s, 'ROOT', root), patch.object(s, 'Source', return_value=world), \
                 patch.object(s, 'baseline_check'), patch.object(s, 'OwnedProcess', world.spawn), \
                 patch.object(s, 'qpc_ns', lambda: int(world.now*1e9)), patch.object(s.time, 'sleep', world.sleep), \
                 patch.object(s, 'refresh', world.refresh), patch.object(s, 'ThreadPoolExecutor', Immediate), \
                 patch.object(s, 'inspect', world.live), patch.object(s, 'wait_process', world.wait), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = s.run(package, out, {'run_id': 'a'*32, 'end_mode': end_mode}, {}, 'business', 'operator')
            self.assertTrue(all(p.closed for p in world.procs))
            self.assertLess(world.now, s.limits_for(end_mode)['total']+1)
            events = [json.loads(line) for line in (out/'events.jsonl').read_text().splitlines()]
            return result, events

    def test_manual_session_runs_beyond_old_caps_then_closes_before_stop_and_restore(self):
        result, events = self.run_case('operator_long', 'operator')
        self.assertTrue(result['execution_complete'], result)
        self.assertGreater(result['monitor']['sample_seconds'], 1260)
        index = {e['event']: i for i, e in enumerate(events)}
        self.assertLess(index['sample_closed'], index['stop_source'])
        self.assertLess(index['stop_source'], index['restore'])

    def test_manual_source_safety_failure_restores_without_passing(self):
        result, _ = self.run_case('operator_safety', 'operator')
        self.assertFalse(result['execution_complete'])
        self.assertIn('source_worker_failed', result['monitor']['failures'])
        self.assertTrue(result['source_stop_verified'])
        self.assertTrue(result['configuration_recovery_verified'])

    def test_natural_source_completion_closes_then_recovers_without_prompt(self):
        result, events = self.run_case('natural')
        self.assertTrue(result['execution_complete'], result)
        names = [e['event'] for e in events]
        self.assertLess(names.index('sample_closed'), names.index('restore'))
        self.assertNotIn('operator_source_stop_prompt', names)
        self.assertFalse(result['n5_passed'] or result['n6_passed'])

    def test_closed_worker_waits_for_parent_restore_without_false_failure(self):
        result, events = self.run_case('slow_parent')
        self.assertTrue(result['execution_complete'], result)
        by_name = {e['event']: e for e in events}
        self.assertLess(by_name['sample_closed']['qpc_ns'], 210e9)
        self.assertGreaterEqual(by_name['restore']['qpc_ns'], 275e9)
        self.assertNotIn('operator_source_stop_prompt', by_name)

    def test_finalizing_without_parent_completion_still_times_out(self):
        result, _ = self.run_case('stuck_parent')
        self.assertFalse(result['execution_complete'])
        self.assertFalse(result['source_stop_verified'])
        self.assertIn('source_stop_timeout', result['monitor']['failures'])

    def test_operator_finish_preserves_order_and_does_not_send_sudo(self):
        result, events = self.run_case('operator')
        self.assertTrue(result['execution_complete'], result)
        self.assertTrue(any(e['event'] == 'operator_source_stop_prompt' for e in events))
        self.assertFalse(any(e['event'] == 'source_request' and e['action'] == 'stop' for e in events))

    def test_raw_factory_tail_is_not_parsed_as_candidate(self):
        result, events = self.run_case('factory_uart')
        self.assertTrue(result['execution_complete'], result)
        self.assertFalse(any(e['event'] == 'live_error' for e in events))

    def test_transport_and_cancel_faults_never_pass(self):
        for case in ('user_cancel', 'uart_stale', 'uart_died', 'candidate_uart_corrupt', 'source_ssh_failed', 'missing_close_ack', 'pipeline_error'):
            with self.subTest(case=case):
                result, _ = self.run_case(case)
                self.assertFalse(result['execution_complete'])
                self.assertTrue(result['configuration_recovery_verified'])

    def test_worker_deadline_and_recovery_failure_remain_failures(self):
        result, _ = self.run_case('worker_stuck')
        self.assertIn('source_stop_timeout', result['monitor']['failures'])
        self.assertFalse(result['source_stop_verified'])
        result, _ = self.run_case('recovery_failed')
        self.assertFalse(result['configuration_recovery_verified'])

    def test_boundary_loss_and_hung_loader_are_bounded(self):
        for case in ('marker_missing', 'marker_early', 'marker_future', 'loader_hung', 'lease_failed'):
            with self.subTest(case=case):
                result, _ = self.run_case(case)
                self.assertFalse(result['execution_complete'])
