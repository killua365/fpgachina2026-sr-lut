"""Observed timeout stays a failure; retain milestones and stop interaction."""
import json
from pathlib import Path
import subprocess
import sys
import socket
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch
import business_session_source as source
from business_session_progress import progress
from test_business_session import fixture


class PersistentQueryTests(unittest.TestCase):
    def test_read_only_server_reuses_process_with_fresh_replies(self):
        code="import os\ndef probe(request):\n stage('test')\n return dict(challenge=request['challenge'],pid=os.getpid())\n"
        with patch.object(source.Path,'read_text',return_value=code):
            server=source.probe_server_code()
        peer=source.ProbeConnection([sys.executable,'-u','-c',server])
        try:
            first=json.loads(peer.query(dict(action='inspect',challenge='a')).stdout)
            second=json.loads(peer.query(dict(action='inspect',challenge='b')).stdout)
            self.assertEqual(first['pid'],second['pid'])
            self.assertEqual((first['challenge'],second['challenge']),('a','b'))
            with self.assertRaisesRegex(RuntimeError,'Read-only'):
                peer.query(dict(action='stop',challenge='c'))
        finally:peer.close()
        self.assertIsNotNone(peer.process.poll())

    def test_persistent_timeout_does_not_return_a_previous_response(self):
        peer=source.ProbeConnection([sys.executable,'-u','-c','import time;time.sleep(30)'])
        try:
            with patch.object(source,'QUERY_TIMEOUT',.1):
                with self.assertRaises(subprocess.TimeoutExpired): peer.query({'challenge':'new'})
        finally:peer.close()

    def test_persistent_adapter_still_rejects_stale_challenge(self):
        _,launch,final=fixture()
        adapter=source.Source(launch,persistent=True)
        response=subprocess.CompletedProcess('probe',0,json.dumps(final),'')
        with patch.object(source,'ProbeConnection') as connection:
            connection.return_value.query.return_value=response
            with self.assertRaisesRegex(ValueError,'Stale or foreign'):adapter.query()
            connection.return_value.close.assert_called_once()
        self.assertIsNone(adapter.binding)


class QueryTests(unittest.TestCase):
    def test_timeout_keeps_partial_stages_without_retry_or_binding(self):
        _, launch, _ = fixture()
        adapter = source.Source(launch)
        stderr = b'debug1: Connection established.\nAuthenticated to board\ndebug1: Sending command: PRIVATE_PAYLOAD\nSRLUT_PROBE {"stage":"python_started","monotonic":12.5}\n'
        with patch.object(source.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ssh',12,stderr=stderr)) as run:
            with self.assertRaisesRegex(RuntimeError,'timed out after 12'):
                adapter.query()
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.kwargs['timeout'],12)
        self.assertIsNone(adapter.binding)
        self.assertEqual(adapter.last_query['outcome'],'timeout')
        self.assertEqual(adapter.last_query['ssh_stages'],['connected','authenticated','command_sent'])
        self.assertEqual(adapter.last_query['remote_stages'][-1]['stage'],'python_started')
        self.assertNotIn('PRIVATE_PAYLOAD',json.dumps(adapter.last_query))

    def test_success_retains_binding_and_timing(self):
        _, launch, final = fixture()
        adapter = source.Source(launch)
        final.update(state='running',challenge='b'*32)
        response = subprocess.CompletedProcess('ssh',0,json.dumps(final),'')
        with patch.object(source.uuid,'uuid4') as uuid, patch.object(source.subprocess,'run',return_value=response):
            uuid.return_value.hex='b'*32
            value=adapter.query()
        self.assertEqual(adapter.binding,final['binding'])
        self.assertEqual(value['query_diagnostics']['outcome'],'accepted')
        self.assertGreaterEqual(value['query_diagnostics']['elapsed_seconds'],0)

    def test_successful_ssh_with_stale_response_is_still_rejected(self):
        _, launch, final = fixture()
        adapter=source.Source(launch)
        response=subprocess.CompletedProcess('ssh',0,json.dumps(final),'')
        with patch.object(source.subprocess,'run',return_value=response):
            with self.assertRaisesRegex(ValueError,'Stale or foreign'):
                adapter.query()
        self.assertEqual(adapter.last_query['outcome'],'rejected')
        self.assertIsNone(adapter.binding)

    def test_transport_failure_preserves_useful_error_without_command(self):
        _, launch, _ = fixture()
        adapter=source.Source(launch)
        response=subprocess.CompletedProcess('ssh',255,'','debug1: Sending command: PRIVATE_PAYLOAD\nConnection reset\n')
        with patch.object(source.subprocess,'run',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'Connection reset'):
                adapter.query()
        self.assertEqual(adapter.last_query['returncode'],255)
        self.assertNotIn('PRIVATE_PAYLOAD',json.dumps(adapter.last_query))


@contextmanager
def loopback_banner(delay):
    """An unauthenticated loopback peer: no board, keys or remote execution."""
    stop = threading.Event()
    accepted = []
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(1)
        def serve():
            while not stop.is_set():
                try:
                    peer, _ = listener.accept()
                except socket.timeout:
                    continue
                with peer:
                    accepted.append(True)
                    if not stop.wait(delay):
                        try:
                            peer.sendall(b'SSH-2.0-n5_deadline_fixture\r\n')
                        except OSError:
                            pass
                    return
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], accepted
        finally:
            stop.set()
            thread.join(2)
            if thread.is_alive():
                raise AssertionError('Loopback peer did not close')


class QueryDeadlineTests(unittest.TestCase):
    def run_loopback(self, delay):
        _, launch, _ = fixture()
        adapter = source.Source(launch)
        real_run = subprocess.run
        with loopback_banner(delay) as (port, accepted):
            def local_run(args, **kwargs):
                # Keep the production SSH timing options and outer timeout.
                # Redirect only the destination; no identity is used before KEX.
                args = list(args)
                args[args.index('xilinx@192.168.2.99')] = 'test@127.0.0.1'
                args[1:1] = ['-F', 'NUL', '-p', str(port)]
                key_index = args.index('-i')
                del args[key_index:key_index+2]
                return real_run(args, **kwargs)
            start = time.monotonic()
            with patch.object(source.subprocess, 'run', side_effect=local_run) as run:
                with self.assertRaises(RuntimeError):
                    adapter.query()
            elapsed = time.monotonic()-start
            self.assertEqual(run.call_count, 1)
            self.assertEqual(len(accepted), 1)
            self.assertEqual(run.call_args.kwargs['timeout'], 12)
            self.assertIsNone(adapter.binding)
        return elapsed, adapter.last_query

    def test_banner_after_five_seconds_reaches_key_exchange(self):
        elapsed, diagnostic = self.run_loopback(6.2)
        self.assertGreaterEqual(elapsed, 6)
        self.assertLess(elapsed, 11)
        self.assertEqual(diagnostic['outcome'], 'rejected')
        self.assertNotIn('timed out', diagnostic['stderr_error_tail'].lower())
        self.assertEqual(diagnostic['ssh_stages'], ['connected'])

    def test_silent_peer_still_expires_within_total_budget(self):
        elapsed, diagnostic = self.run_loopback(30)
        self.assertGreaterEqual(elapsed, 11)
        self.assertLess(elapsed, 14)
        self.assertIn(diagnostic['outcome'], ('timeout', 'rejected'))
        self.assertEqual(diagnostic['remote_stages'], [])


class ProgressTests(unittest.TestCase):
    def test_controller_failure_closure_or_finalizing_blocks_interaction(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]/'build') as name:
            root=Path(name)
            worker=dict(state='running',worker=dict(phase='interactive_business'))
            monitor=dict(state='sampling',failures=[],sample_close=None)
            (root/'source_0001.json').write_text(json.dumps(worker))
            for state, failures, closed, allowed in (
                ('sampling',[],None,True),('sampling',['source_probe_failed'],None,False),
                ('sampling',[],5,False),('stopping_source',[],5,False),('warming',[],None,False)):
                with self.subTest(state=state,failures=failures,closed=closed):
                    (root/'live_status.json').write_text(json.dumps(dict(monitor=dict(
                        state=state,failures=failures,sample_close=closed))))
                    self.assertEqual(progress(root)['further_interactions_allowed'],allowed)
            (root/'live_status.json').write_text(json.dumps(dict(monitor=monitor)))
            (root/'source_0002.json').write_text(json.dumps(dict(worker,state='finalizing')))
            self.assertFalse(progress(root)['further_interactions_allowed'])
