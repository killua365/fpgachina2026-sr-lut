import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'board/host'))
import business_session_remote as remote
from business_session_source import Source, CONTRACT
from business_session_policy import LIMITS, Monitor
from business_session_live import inspect
from analyze_business_session import timeline, bind_events
from supervise_business_session import baseline_check
from test_business_diagnostic import record, wire
sys.path.insert(0, str(ROOT/'board/pynq'))
from prepare_business_session import source_start_check


def fixture():
    c = json.loads(CONTRACT.read_text('utf-8'))
    launch = dict(schema='n5_business_launch_v1', run_id='a'*32, **{k: c[k] for k in
        ('package_sha256', 'package_manifest_sha256', 'source_commit', 'remote_package')},
        remote_results='/home/xilinx/n5_business_results_'+'a'*32, seconds=180,
        profile_seconds=0, camera_device='/dev/video0', supervisor_deadline_seconds=420)
    worker = dict(kind='hdmi_business_worker', run_id=launch['run_id'], manifest_sha256=c['package_manifest_sha256'],
        source_commit=c['source_commit'], boot_id='boot', card_cid=remote.CID, started_monotonic=1.,
        requested_seconds=180, profile_seconds=0, camera_device='/dev/video0', business_profile={'name':'nexys_dp_bridge_v1'},
        phase='finished', closed=True, display_stopped=True, sr_dma_stopped=True)
    binding = dict(worker_pid=12, boot_id='boot', started_monotonic=1.)
    final = dict(state='finished', run_id=launch['run_id'], remote_results=launch['remote_results'], boot_id='boot',
        binding=binding, worker=worker, challenge='b'*32, portal='active', candidate_service='inactive',
        supervisor=dict(kind='hdmi_business_supervisor', run_id=launch['run_id'], source_commit=c['source_commit'],
            manifest_sha256=c['package_manifest_sha256'], requested_seconds=180, profile_seconds=0,
            worker_reaped=True, worker_exit_code=0, worker_pid=12, legacy_restored=True, hardware_closed=True),
        legacy_status=dict(candidate=False, hardware_identity=dict(identity_match=True, manifest_match=True,
            release_archive_sha256=remote.FORMAL_SHA)))
    return c, launch, final


class SessionTests(unittest.TestCase):
    def test_source_start_requires_current_live_nexys_session(self):
        _, launch, _ = fixture()
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as name:
            folder = Path(name)
            (folder/'board').mkdir()
            (folder/'control').mkdir()
            (folder/'plan.json').write_text(json.dumps(dict(schema='n5_business_session_v1', run_id=launch['run_id'])))
            (folder/'result.json').write_text(json.dumps(dict(state='running', run_id=launch['run_id'])))
            live = dict(monitor=dict(state='sampling', failures=[]), input=dict(available=True, link_ready=True, error_flags=0))
            (folder/'live_status.json').write_text(json.dumps(live))
            (folder/'board/active.txt').write_text('candidate=26101201\nrun_id='+launch['run_id']+'\n')
            now = (folder/'live_status.json').stat().st_mtime
            self.assertTrue(source_start_check(folder, launch, now))
            # A live writer can replace the file during the read. The age
            # clock must be sampled after reading that file's own timestamp.
            def after_read():
                self.assertTrue(loaded.called)
                return now
            with patch('prepare_business_session.json.load', wraps=json.load) as loaded, \
                 patch('prepare_business_session.time.time', side_effect=after_read):
                self.assertTrue(source_start_check(folder, launch))
            with self.assertRaises(ValueError):
                source_start_check(folder, launch, now-1)
            with self.assertRaises(ValueError):
                source_start_check(folder, launch, now+11)
            with self.assertRaises(ValueError):
                source_start_check(folder, dict(launch, run_id='c'*32), now)
            for video in (dict(available=False), dict(available=True, link_ready=False, error_flags=0),
                          dict(available=True, link_ready=True, error_flags=1)):
                (folder/'live_status.json').write_text(json.dumps(dict(live, input=video)))
                with self.assertRaises(ValueError):
                    source_start_check(folder, launch, (folder/'live_status.json').stat().st_mtime)
            (folder/'live_status.json').write_text(json.dumps(live))
            now = (folder/'live_status.json').stat().st_mtime
            (folder/'board/recovery_started_qpc.txt').write_text('10')
            with self.assertRaises(ValueError):
                source_start_check(folder, launch, now)

    def test_launch_rejects_old_package_duration_and_foreign_directory(self):
        c, launch, _ = fixture()
        self.assertEqual(remote.validate_launch(launch, c), launch)
        for key, value in (('package_sha256', '0'*64), ('seconds', 301), ('seconds', True),
                           ('remote_results', '/home/xilinx/old'), ('profile_seconds', 10),
                           ('supervisor_deadline_seconds', 1200)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                remote.validate_launch(dict(launch, **{key:value}), c)

    def test_current_binding_required_before_final_report(self):
        _, launch, final = fixture()
        source = Source(launch)
        with self.assertRaises(ValueError):
            source.accept(final, 'b'*32)
        source.accept(dict(final, state='running'), 'b'*32)
        source.accept(final, 'b'*32)
        for response in (dict(final, challenge='c'*32), dict(final, binding=dict(final['binding'], worker_pid=99)),
                         dict(final, state='absent')):
            with self.assertRaises(ValueError):
                source.accept(response, 'b'*32)

    def test_close_requires_dma_worker_and_live_original_service(self):
        _, launch, value = fixture()
        self.assertTrue(remote.verify_final(value, launch, value['binding']))
        for group, key, bad in (('worker', 'display_stopped', False), ('worker', 'sr_dma_stopped', False),
                               ('worker', 'retained_dma_error', 'halt failed'), ('worker', 'card_cid', 'wrong'),
                               ('supervisor', 'worker_reaped', False), ('supervisor', 'worker_pid', 99),
                               ('supervisor', 'legacy_restored', False), ('legacy_status', 'candidate', True)):
            broken = copy.deepcopy(value)
            broken[group][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                remote.verify_final(broken, launch, value['binding'])

    def test_finalizing_needs_live_binding_and_closed_hardware(self):
        _, launch, value = fixture()
        value['state'] = 'finalizing'
        value['supervisor']['worker_reaped'] = False
        value['supervisor']['legacy_restored'] = False
        source = Source(launch)
        with self.assertRaises(ValueError):
            source.accept(value, 'b'*32)
        source.accept(dict(value, state='running'), 'b'*32)
        source.accept(value, 'b'*32)
        for key in ('closed', 'display_stopped', 'sr_dma_stopped'):
            broken = copy.deepcopy(value)
            broken['worker'][key] = False
            with self.subTest(key=key), self.assertRaises(ValueError):
                source.accept(broken, 'b'*32)
        with self.assertRaises(ValueError):
            source.accept(dict(value, state='finished'), 'b'*32)

    def test_probe_uses_closed_worker_report_while_parent_restores(self):
        import hashlib
        contract, launch, value = fixture()
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as name:
            root = Path(name)
            package, results = root/'package', root/'results'
            package.mkdir(); (results/'worker').mkdir(parents=True)
            data = b'{}'
            (package/'hdmi_business_package.json').write_bytes(data)
            launch['package_manifest_sha256'] = hashlib.sha256(data).hexdigest()
            contract['package_manifest_sha256'] = launch['package_manifest_sha256']
            value['worker']['manifest_sha256'] = launch['package_manifest_sha256']
            active = dict(value['worker'], phase='interactive_business', closed=False)
            (results/'worker/status.json').write_text(json.dumps(active))
            (results/'worker/report.json').write_text(json.dumps(value['worker']))
            (results/'supervisor_status.json').write_text(json.dumps(dict(worker_pid=12, worker_reaped=False)))
            (root/'boot').write_text('boot')
            def path(name):
                mapping = {launch['remote_package']: package, launch['remote_results']: results,
                           '/proc/sys/kernel/random/boot_id': root/'boot'}
                self.assertIn(name, mapping, 'Must not read a disappeared worker PID')
                return mapping[name]
            request = dict(launch=launch, contract=contract, action='inspect', binding=value['binding'], challenge='b'*32)
            with patch.object(remote, 'Path', side_effect=path):
                result = remote.probe(request)
            self.assertEqual(result['state'], 'finalizing')
            self.assertTrue(result['worker']['closed'])

    def test_fresh_baseline_is_run_bound(self):
        _, launch, _ = fixture()
        now = datetime.now(timezone.utc)
        value = dict(recorded_host_utc=now.isoformat(), authority='user', run_id=launch['run_id'],
                     original_pattern_normal=True, osd='1280x1024@60', onsite_ready=True, cables_verified=True)
        baseline_check(value, launch, now)
        for bad in (dict(value, run_id='c'*32), dict(value, authority='tool'),
                    dict(value, recorded_host_utc=(now-timedelta(hours=1)).isoformat()), dict(value, onsite_ready=False)):
            with self.assertRaises(ValueError):
                baseline_check(bad, launch, now)

    def test_business_only_limits_fit_independent_hold(self):
        self.assertLess(sum(LIMITS[k] for k in ('load', 'warmup', 'window', 'source_stop'))+2*LIMITS['acknowledgement'], LIMITS['hold'])
        self.assertEqual(LIMITS['total'], LIMITS['hold']+LIMITS['recovery'])
        with self.assertRaises(ValueError):
            Monitor(0, 'dp')

    def test_recent_srb5_rejects_wrong_identity_crc_and_gaps(self):
        rows = [record(i) for i in range(4)]
        data = b'factory'+b''.join(map(wire, rows))+b'SRB5'
        status = inspect(data)
        self.assertEqual((status['new_results'], status['generation']), (7, 7))
        startup = record(0)
        startup['words'][40:] = [0]*52
        self.assertTrue(inspect(wire(startup))['initializing'])
        startup['words'][30] = 1_000_000_000
        with self.assertRaises(ValueError):
            inspect(wire(startup))
        startup['words'][30] = 100_000_000
        startup['words'][40:42] = [0x53524944, 1]
        startup['words'][35] = 0x33  # UI clock exists, DDR calibration is still pending.
        self.assertTrue(inspect(wire(startup))['initializing'])
        startup['words'][30] = 1_000_000_000
        with self.assertRaises(ValueError):
            inspect(wire(startup))
        for broken in (rows[0:1]+rows[2:],):
            with self.assertRaises(ValueError):
                inspect(b''.join(map(wire, broken)))
        for field, bad in ((0, 0x26100901), (41, 3)):
            broken = copy.deepcopy(rows)
            broken[-1]['words'][field] = bad
            with self.assertRaises(ValueError):
                inspect(b''.join(map(wire, broken)))
        broken = bytearray(data)
        broken[100] ^= 1
        with self.assertRaises(ValueError):
            inspect(bytes(broken))

    def test_complete_raw_stream_and_source_join(self):
        rows = [record(i) for i in range(4)]
        data = b'old'+b''.join(map(wire, rows))
        chunks = [dict(count=384 if i == 0 else 381, total_bytes=3+(i+1)*381, qpc_ticks=(i+1)*100_000_000) for i in range(4)]
        parsed, _, meta = timeline(data, chunks, 1_000_000_000, 400_000_000)
        source = [dict(generation=7, sequence=n, mode=2, scale=4) for n in range(1, 8)]
        self.assertEqual(len(bind_events(parsed, source)), 7)
        self.assertEqual(meta['prefix_bytes'], 3)
        with self.assertRaises(ValueError):
            bind_events(parsed, source[:-1])
        with self.assertRaises(ValueError):
            bind_events(parsed, [dict(f, scale=2) for f in source])
        with self.assertRaises(ValueError):
            timeline(data[:-1], chunks, 1_000_000_000, 400_000_000)

    def test_recent_status_recovers_alignment_without_accepting_corrupt_session(self):
        from decode_business_diagnostic import decode
        frames = [wire(record(i)) for i in range(100)]
        # A transport deletion destroys the original fixed byte alignment.
        frames[20] = frames[20][:100]+frames[20][149:]
        with self.assertRaises(ValueError):
            inspect(b''.join(frames[:40]))
        raw = b''.join(frames)
        self.assertEqual(inspect(raw)['sequence'], 99)
        self.assertTrue(decode(raw)['errors'])
        chunks = [dict(count=len(raw), total_bytes=len(raw), qpc_ticks=1)]
        with self.assertRaises(ValueError):
            timeline(raw, chunks, 1_000_000_000, 1)
        monitor = Monitor(0, 'business')
        monitor.fail('live_telemetry_invalid')
        monitor.tick(1, active=True)
        self.assertIn('live_telemetry_invalid', monitor.failures)

    def test_recent_resynchronization_still_rejects_new_damage_and_sequence_gaps(self):
        frames = [wire(record(i)) for i in range(100)]
        for broken in (frames[:95]+frames[96:],
                       frames[:95]+[frames[95][:100]+frames[95][149:]]+frames[96:]):
            with self.subTest(), self.assertRaises(ValueError):
                inspect(b''.join(broken))
        for tail in (b'', b'S', b'SRB5', frames[0][:127]):
            self.assertEqual(inspect(b''.join(frames)+tail)['sequence'], 99)

    def test_journal_loss_mutation_and_source_replay_fail(self):
        rows = [record(i) for i in range(4)]
        source = [dict(generation=7, sequence=n, mode=2, scale=4) for n in range(1, 8)]
        with self.assertRaises(ValueError):
            bind_events([rows[0], rows[-1]], source)
        with self.assertRaises(ValueError):
            bind_events(rows, source+source[:1])
        broken = copy.deepcopy(rows)
        broken[1]['words'][40+20+4] += 1
        with self.assertRaises(ValueError):
            bind_events(broken, source)
