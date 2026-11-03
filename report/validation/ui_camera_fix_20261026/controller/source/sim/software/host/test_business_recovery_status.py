import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hdmi_business_app import restore_legacy
from legacy_service_rollback import PORTAL


class RecoveryStatusTests(unittest.TestCase):
    def exercise(self, fail=None):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            report = dict(run_id='test', worker_pid=42, worker_reaped=True,
                          worker_exit_code=0, legacy_restored=False, errors=[])
            def snapshot():
                return json.loads((destination/'recovery_status.json').read_text())
            def active(service):
                if service == PORTAL:
                    self.assertEqual(snapshot()['recovery_phase'], 'checking_legacy_active')
                    return 'active'
                self.assertEqual(snapshot()['recovery_phase'], 'checking_candidate_stopped')
                return 'active' if fail == 'candidate' else 'inactive'
            def start(command, **kwargs):
                state = snapshot()
                self.assertEqual(state['recovery_phase'], 'starting_legacy_service')
                self.assertTrue(state['worker_reaped'])
                self.assertFalse(state['legacy_restored'])
                if fail == 'start':
                    raise subprocess.TimeoutExpired(command, kwargs['timeout'])
            def identity(port, candidate):
                self.assertEqual(snapshot()['recovery_phase'], 'waiting_legacy_identity')
                if fail == 'identity':
                    raise RuntimeError('identity mismatch')
                return {'verified': True}
            with patch('legacy_service_rollback.active', side_effect=active), \
                 patch('legacy_service_rollback.require_bootpy_completed', return_value={
                     'ExecMainExitTimestampMonotonic': '123'}), \
                 patch('legacy_service_rollback.wait_status', side_effect=identity), \
                 patch('hdmi_business_app.subprocess.run', side_effect=start) as starter:
                restore_legacy(destination, report)
            self.assertEqual(snapshot()['recovery_phase'], 'restore_failed' if fail else 'restore_complete')
            self.assertEqual(report['legacy_restored'], fail is None)
            self.assertEqual(bool(report['errors']), fail is not None)
            if fail == 'candidate':
                starter.assert_not_called()
            self.assertEqual((destination/'legacy_restored.json').exists(), fail is None)

    def test_success_records_identity_before_completion(self):
        self.exercise()

    def test_start_timeout_remains_failure(self):
        self.exercise('start')

    def test_wrong_identity_remains_failure(self):
        self.exercise('identity')

    def test_active_candidate_prevents_start(self):
        self.exercise('candidate')


if __name__ == '__main__':
    unittest.main()
