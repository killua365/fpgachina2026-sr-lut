"""Offline launcher qualification: no SSH, board access, service or FPGA use."""
import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_session as demo
from business_session_policy import Monitor
from supervise_business_session import baseline_check

RUN_ID = '1234567890abcdef1234567890abcdef'


def running(state='sampling', *, closed=None, failures=None):
    return ({'run_id': RUN_ID, 'state': 'running'},
            {'monitor': {'state': state, 'sample_close': closed, 'failures': failures or []}})


class PolicyTests(unittest.TestCase):
    def test_start_requires_ready_and_live_controller(self):
        for ready, exit_code, stop, failures in ((False, None, False, []),
                (True, 1, False, []), (True, None, True, []), (True, None, False, ['link'])):
            result, live = running('warming', failures=failures)
            actions = demo.DemoPolicy().actions(result=result, live=live, ready=ready,
                                                controller_exit=exit_code, stop=stop)
            self.assertNotIn('start_source', actions)
        result, live = running('warming')
        self.assertEqual(demo.DemoPolicy().actions(result=result, live=live, ready=True), ['start_source'])

    def test_ordered_ctrl_c_uses_real_monitor_ack(self):
        monitor = Monitor(0, 'business', 'operator')
        monitor.tick(1, active=True)
        monitor.tick(31)
        monitor.tick(32, start_ack=True)
        policy = demo.DemoPolicy()
        policy.source_started = True
        result = {'state': 'running'}
        actions = policy.actions(result=result, live={'monitor': monitor.summary()}, stop=True)
        self.assertEqual(actions, ['close'])
        policy.requested = True
        monitor.tick(100, close=True)
        self.assertEqual(policy.actions(result=result, live={'monitor': monitor.summary()}, stop=True), [])
        monitor.tick(101, close_ack=True)
        self.assertEqual(policy.actions(result=result, live={'monitor': monitor.summary()}, stop=True), ['interrupt_source'])
        policy.interrupted = True
        self.assertEqual(policy.actions(result=result, live={'monitor': monitor.summary()}, stop=True), [])

    def test_cancel_startup_and_no_source_launch(self):
        result, live = running('warming')
        policy = demo.DemoPolicy()
        self.assertEqual(policy.actions(result=result, live=live, ready=True, stop=True), ['cancel'])
        policy.requested = True
        self.assertEqual(policy.actions(result=result, live=live, ready=True, stop=True), [])

    def test_closing_never_starts_source(self):
        for state in ('closing', 'stopping_source', 'restoring', 'done'):
            result, live = running(state)
            self.assertNotIn('start_source', demo.DemoPolicy().actions(result=result, live=live, ready=True))

    def test_failure_ack_timeout_is_not_reported_as_ordered(self):
        policy = demo.DemoPolicy()
        policy.source_started = True
        result, live = running('stopping_source')
        self.assertEqual(policy.actions(result=result, live=live), [])
        live['monitor']['failures'] = ['close_ack_timeout']
        self.assertEqual(policy.actions(result=result, live=live), ['interrupt_source'])
        self.assertIsNone(live['monitor']['sample_close'])

    def test_source_exit_requests_close_without_claiming_recovery(self):
        policy = demo.DemoPolicy()
        policy.source_started = True
        result, live = running()
        self.assertEqual(policy.actions(result=result, live=live, source_exit=1), ['close'])

    def test_controller_crash_stops_source_but_marks_order_unknown(self):
        policy = demo.DemoPolicy()
        policy.source_started = True
        result, live = running()
        self.assertEqual(policy.actions(result=result, live=live, controller_exit=1),
                         ['controller_lost', 'interrupt_source'])

    def test_completed_remote_recovery_can_precede_local_ssh_exit(self):
        policy = demo.DemoPolicy()
        policy.source_started = True
        result = dict(state='ended', source_stop_verified=True)
        self.assertEqual(policy.actions(result=result, live={}, controller_exit=0), [])


class EvidenceTests(unittest.TestCase):
    def test_status_read_retries_transient_windows_sharing_error(self):
        with patch.object(Path, 'read_text', side_effect=[PermissionError('sharing'),
                FileNotFoundError('replacement'), '{"state":"new"}']) as reader, \
                patch.object(demo.time, 'sleep'):
            self.assertEqual(demo.read_json('status.json'), {'state': 'new'})
            self.assertEqual(reader.call_count, 3)

    def test_persistent_status_read_failure_remains_failure(self):
        with patch.object(Path, 'read_text', side_effect=PermissionError('persistent')) as reader, \
                patch.object(demo.time, 'sleep'):
            with self.assertRaises(PermissionError):
                demo.read_json('status.json')
            self.assertEqual(reader.call_count, 6)

    def test_malformed_status_is_not_replaced_with_cached_success(self):
        with patch.object(Path, 'read_text', return_value='{broken') as reader:
            with self.assertRaises(json.JSONDecodeError):
                demo.read_json('status.json')
            self.assertEqual(reader.call_count, 1)

    def test_launcher_cannot_borrow_old_qualification_after_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            files=[]
            for name in demo.LAUNCHER_FILES:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
                files.append(dict(path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            qualification=root/'report/validation/fixture/result.json'
            qualification.parent.mkdir(parents=True)
            demo.write_new(qualification,dict(passed=True,hardware_access=False,tests=23,failures=0,errors=0,
                skipped=0,inputs={item['path']:item['sha256'] for item in files}))
            tools=root/demo.TOOLS.relative_to(demo.ROOT)/'manifest.json'
            tools.parent.mkdir(parents=True);tools.write_text('{}')
            manifest=root/demo.LAUNCHER_MANIFEST;manifest.parent.mkdir(parents=True,exist_ok=True)
            demo.write_new(manifest,dict(files=files,qualification=qualification.relative_to(root).as_posix(),
                qualification_sha256=hashlib.sha256(qualification.read_bytes()).hexdigest(),
                session_tool_manifest_sha256=hashlib.sha256(tools.read_bytes()).hexdigest()))
            self.assertTrue(demo.verify_launcher(root))
            (root/demo.LAUNCHER_FILES[0]).write_text('changed')
            with self.assertRaisesRegex(ValueError,'Demo launcher changed'):
                demo.verify_launcher(root)

    def test_cancel_before_start_does_not_prepare_session(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(demo,'ROOT',Path(tmp)), \
                patch.object(demo,'local_check',return_value={'passed':True}),patch.object(demo,'prepare') as prepare:
            self.assertEqual(demo.run_demo(True,threading.Event(),say=lambda _:None,
                                         interrupt_requested=lambda:True),1)
            prepare.assert_not_called()

    def test_baseline_needs_explicit_confirmation_and_binds_run(self):
        with self.assertRaises(ValueError):
            demo.baseline({'run_id': RUN_ID}, False)
        value = demo.baseline({'run_id': RUN_ID}, True)
        baseline_check(value, {'run_id': RUN_ID})
        with self.assertRaises(ValueError):
            baseline_check(value, {'run_id': '0' * 32})

    def test_old_or_foreign_controller_cannot_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            demo.write_new(run / 'plan.json', {'run_id': RUN_ID})
            demo.write_new(run / 'result.json', {'run_id': '0'*32, 'state': 'running'})
            with self.assertRaises(ValueError):
                demo.request_end(run, RUN_ID, 'close')
            self.assertFalse((run / 'close.request').exists())

    def test_close_is_idempotent_and_never_overwrites_foreign_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            demo.write_new(run / 'plan.json', {'run_id': RUN_ID})
            demo.write_new(run / 'result.json', {'run_id': RUN_ID, 'state': 'running'})
            self.assertTrue(demo.request_end(run, RUN_ID, 'close'))
            self.assertFalse(demo.request_end(run, RUN_ID, 'close'))
            (run / 'close.request').write_text('foreign', encoding='ascii')
            with self.assertRaises(ValueError):
                demo.request_end(run, RUN_ID, 'close')

    def test_prior_incomplete_session_blocks_new_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / 'board/acceptance/example'
            run.mkdir(parents=True)
            pointer = root / 'pointer.json'
            demo.write_new(pointer, {'run': str(run), 'run_id': RUN_ID, 'controller_started': True})
            with self.assertRaises(RuntimeError):
                demo.prior_session_check(pointer, root)
            demo.write_new(run/'result.json', {'run_id': RUN_ID, 'state': 'ended',
                'configuration_recovery_verified': True, 'source_stop_verified': False})
            with self.assertRaises(RuntimeError):
                demo.prior_session_check(pointer, root)
            demo.replace_json(run/'result.json', {'run_id': RUN_ID, 'state': 'ended',
                'configuration_recovery_verified': True, 'source_stop_verified': True})
            demo.prior_session_check(pointer, root)

    def test_never_started_source_is_distinct_from_unknown_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root/'board/acceptance/example'
            run.mkdir(parents=True)
            pointer = root/'pointer.json'
            demo.write_new(pointer, dict(run=str(run), run_id=RUN_ID, controller_started=True,
                                        source_launch_attempted=False))
            demo.write_new(run/'result.json', dict(run_id=RUN_ID, state='ended',
                configuration_recovery_verified=True, source_stop_verified=False))
            demo.prior_session_check(pointer, root)

    @unittest.skipUnless(os.name == 'nt', 'Windows OS lock')
    def test_second_launcher_cannot_acquire_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp)/'lock'
            with demo.SessionLock(lock):
                with self.assertRaises(RuntimeError):
                    with demo.SessionLock(lock):
                        self.fail('Second owner')
            with demo.SessionLock(lock):
                pass

    def test_check_does_not_prepare_or_start_hardware(self):
        with patch.object(demo, 'local_check', return_value={'passed': True}) as check, \
                patch.object(demo, 'run_demo') as run, patch.object(demo, 'prepare') as prepare, \
                patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(demo.main(['--check']), 0)
        check.assert_called_once()
        run.assert_not_called()
        prepare.assert_not_called()


class Keyboard:
    def __init__(self, text):
        self.text = list(text)
    def kbhit(self):
        return bool(self.text)
    def getwch(self):
        return self.text.pop(0)


class PasswordTests(unittest.TestCase):
    def test_normal_control_keys_ignore_text_and_detect_ctrl_c(self):
        keyboard=Keyboard('noise\x03')
        control=demo.ConsoleInput();control.special=False
        with patch('msvcrt.kbhit',keyboard.kbhit),patch('msvcrt.getwch',keyboard.getwch):
            self.assertTrue(control.interrupted())
            self.assertFalse(control.interrupted())

    def test_console_mode_restored_on_failure(self):
        control=demo.ConsoleInput()
        class Kernel:
            def __init__(self):self.restored=None
            def SetConsoleMode(self,handle,value):self.restored=(handle,value);return 1
        control.kernel=Kernel();control.handle=7
        control.original=type('Value',(),{'value':31})()
        control.__exit__(RuntimeError,RuntimeError('fixture'),None)
        self.assertEqual(control.kernel.restored,(7,31))

    def test_password_not_echoed_and_backspace_supported(self):
        output = []
        password = demo.read_password(threading.Event(), lambda: True,
            keyboard=Keyboard('test-x\bsecret\r'), output=lambda value, **_: output.append(value))
        self.assertEqual(password, 'test-secret')
        self.assertNotIn(password, ''.join(output))

    def test_ctrl_c_during_password_requests_ordered_stop(self):
        stop = threading.Event()
        self.assertIsNone(demo.read_password(stop, lambda: True, keyboard=Keyboard('partial\x03'),
                                             output=lambda *_, **__: None))
        self.assertTrue(stop.is_set())

    def test_controller_closure_aborts_password_input(self):
        self.assertIsNone(demo.read_password(threading.Event(), lambda: False,
            keyboard=Keyboard('must-not-be-read'), output=lambda *_, **__: None))


@unittest.skipUnless(os.name == 'nt', 'Actual Windows PowerShell pipe transport')
class TransportTests(unittest.TestCase):
    def test_hidden_real_windows_console_handles_ctrl_c_and_restores_mode(self):
        with tempfile.TemporaryDirectory(prefix='sr lut console mode ') as tmp:
            folder=Path(tmp);script=folder/'console_probe.py';receipt=folder/'receipt.json'
            code=r'''
import ctypes,json,sys,time
from ctypes import wintypes as W
from pathlib import Path
sys.path.insert(0,RUNTIME)
from demo_session import ConsoleInput
class Key(ctypes.Structure):
    _fields_=[('down',W.BOOL),('repeat',W.WORD),('vk',W.WORD),('scan',W.WORD),('char',W.WCHAR),('controls',W.DWORD)]
class Event(ctypes.Union):
    _fields_=[('key',Key),('padding',ctypes.c_byte*16)]
class Record(ctypes.Structure):
    _fields_=[('type',W.WORD),('event',Event)]
with ConsoleInput() as console:
    k=console.kernel;mode=W.DWORD();k.GetConsoleMode(console.handle,ctypes.byref(mode))
    assert mode.value & 1 == 0
    event=Record();event.type=1;event.event.key=Key(True,1,0x43,0,'\x03',8)
    k.WriteConsoleInputW.argtypes=[W.HANDLE,ctypes.POINTER(Record),W.DWORD,ctypes.POINTER(W.DWORD)]
    count=W.DWORD()
    assert k.WriteConsoleInputW(console.handle,ctypes.byref(event),1,ctypes.byref(count))
    until=time.monotonic()+3;pressed=False
    while time.monotonic()<until and not pressed:
        pressed=console.interrupted();time.sleep(.01)
    assert pressed
restored=W.DWORD();k.GetConsoleMode(console.handle,ctypes.byref(restored))
Path(OUTPUT).write_text(json.dumps(dict(ctrl_c_read=pressed,restored=restored.value==console.original.value)))
'''
            code='RUNTIME='+repr(str(Path(demo.__file__).parent))+'\nOUTPUT='+repr(str(receipt))+'\n'+code
            script.write_text(code,encoding='utf-8')
            startup=subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow=0  # SW_HIDE: never display the test console.
            process=subprocess.Popen([sys.executable,'-X','utf8','-E','-B',str(script)],
                creationflags=subprocess.CREATE_NEW_CONSOLE,startupinfo=startup,close_fds=True)
            try:
                self.assertEqual(process.wait(timeout=10),0)
                self.assertEqual(demo.read_json(receipt),dict(ctrl_c_read=True,restored=True))
            finally:
                if process.poll() is None:
                    process.kill();process.wait(timeout=5) # Local console fixture only.

    def test_actual_powershell_native_input_password_and_etx(self):
        # A local Python fixture substitutes for SSH. No board/network command.
        with tempfile.TemporaryDirectory(prefix='sr lut console ') as tmp:
            folder = Path(tmp)
            child = folder/'child.py'
            receipt = folder/'receipt.json'
            child.write_text("import sys,json\nfrom pathlib import Path\n"
                "print('[sudo] password for xilinx: ',end='',flush=True)\n"
                "password=sys.stdin.buffer.readline()\n"
                "print('READY',flush=True)\n"
                "stop=sys.stdin.buffer.read(1)\n"
                "Path(sys.argv[1]).write_text(json.dumps({'password_ok':password==b'offline-test\\n','stop':list(stop)}))\n",
                encoding='utf-8')
            script = folder/'source.ps1'
            def ps_quote(value):
                return "'"+str(value).replace("'", "''")+"'"
            script.write_text("$ErrorActionPreference='Stop'\n"
                "$Host.UI.RawUI.WindowTitle='Offline fixture'\n"
                + '& '+ps_quote(sys.executable)+' -X utf8 -E -B '+ps_quote(child)+' '+ps_quote(receipt)+'\n'
                + 'exit $LASTEXITCODE\n', encoding='utf-8-sig')
            process = subprocess.Popen([str(demo.powershell_path()), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=demo.process_flags())
            console = demo.SourceConsole(process)
            try:
                deadline = time.monotonic()+10
                while not console.poll_prompt() and time.monotonic()<deadline:
                    if process.poll() is not None:
                        self.fail('Fixture exited before prompt')
                    time.sleep(.05)
                self.assertEqual(console.prompts, 1)
                console.password('offline-test')
                time.sleep(.2)
                self.assertIsNone(process.poll())
                console.interrupt()
                self.assertEqual(process.wait(timeout=10), 0)
                result = demo.read_json(receipt)
                self.assertEqual(result, {'password_ok': True, 'stop': [3]})
            finally:
                if process.poll() is None:
                    process.kill()  # Only the local fixture, never production code.
                    process.wait(timeout=5)
                console.close()

    def test_launcher_help_works_from_unrelated_directory(self):
        root = Path(demo.ROOT)
        result = subprocess.run(['cmd.exe', '/d', '/c', str(root/'demo.cmd'), '--help'],
            cwd=os.environ['TEMP'], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertIn(b'--ready', result.stdout)


@unittest.skipUnless(os.name == 'nt', 'Actual Windows processes with simulated devices')
class LifecycleTests(unittest.TestCase):
    def exercise(self, *, fail_after_ready=False, pre_source_failure=False, campaign=False):
        with tempfile.TemporaryDirectory(prefix='sr lut lifecycle ') as tmp:
            root = Path(tmp)
            nexys = root/'board/host'
            nexys.mkdir(parents=True)
            folder = root/'board/acceptance/2026-10-17/n5_source_launches'
            folder.mkdir(parents=True)
            launch = folder/('launch_'+RUN_ID+'.json')
            demo.write_new(launch, dict(run_id=RUN_ID))
            run = folder.parent/('n5_demo_'+RUN_ID)
            # Complete lifecycle fixture; never imports a hardware/API module.
            controller_text = r'''
import argparse,json,time,sys
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('tools');p.add_argument('run',type=Path)
p.add_argument('--launch',type=Path);p.add_argument('--baseline');p.add_argument('--case');p.add_argument('--source-stop-mode')
a=p.parse_args();r=a.run;rid=json.loads(a.launch.read_text())['run_id'];r.mkdir()
(r/'board').mkdir();(r/'control').mkdir()
def write(name,v):
    path=r/name;t=path.with_suffix('.temp');t.write_text(json.dumps(v))
    for attempt in range(10):
        try:t.replace(path);return
        except PermissionError:
            if attempt==9:raise
            time.sleep(.02)
result=dict(run_id=rid,state='running')
monitor=dict(state='sampling',failures=[],sample_close=None)
write('plan.json',dict(schema='n5_business_session_v1',run_id=rid))
write('result.json',result)
(r/'board/active.txt').write_bytes(('candidate=26101201\nrun_id='+rid+'\n').encode())
limit=time.monotonic()+15
closed=False
while time.monotonic()<limit:
    if PRE_SOURCE_FAILURE:
        monitor.update(state='stopping_source',failures=['link_not_ready'])
        write('live_status.json',dict(monitor=monitor,input=dict(available=True,link_ready=False,error_flags=0)))
        time.sleep(.25)
        result.update(state='ended',source_stop_verified=False,configuration_recovery_verified=True,monitor=monitor)
        write('result.json',result);sys.exit(1)
    if (r/'source-ready.json').exists():
        write('source_0001.json',dict(run_id=rid,state='running',worker=dict(phase='camera_performance_complete' if CAMPAIGN else 'interactive_business')))
    if not closed and ((r/'close.request').exists() or (r/'cancel.request').exists()):
        monitor['state']='closing'
        write('live_status.json',dict(monitor=monitor,input=dict(available=True,link_ready=True,error_flags=0)))
        time.sleep(.2)
        monitor.update(state='stopping_source',sample_close=time.monotonic())
        closed=True
    write('live_status.json',dict(monitor=monitor,input=dict(available=True,link_ready=True,error_flags=0)))
    if (r/'source-stopped.json').exists():
        receipt=json.loads((r/'source-stopped.json').read_text())
        monitor['state']='done'
        result.update(state='ended',source_stop_verified=True,configuration_recovery_verified=True,monitor=monitor,execution_complete=True)
        write('result.json',result)
        sys.exit(0 if receipt['ordered'] else 2)
    time.sleep(.05)
raise SystemExit('fixture deadline')
'''
            controller_text = 'PRE_SOURCE_FAILURE='+repr(pre_source_failure)+'\nCAMPAIGN='+repr(campaign)+'\n'+controller_text
            (nexys/'supervise_business_session.py').write_text(controller_text, encoding='utf-8')
            child = root/'source.py'
            child.write_text(r'''
import json,sys,time
from pathlib import Path
r=Path(sys.argv[1])
print('[sudo] password for xilinx: ',end='',flush=True)
secret=b''
while not secret.endswith(b'\n'):
    byte=sys.stdin.buffer.read(1)
    if byte in (b'\x03',b''):
        m=json.loads((r/'live_status.json').read_text())['monitor']
        temp=r/'source-stopped.temp'
        temp.write_text(json.dumps(dict(ordered=m['sample_close'] is not None,byte=list(byte),password_aborted=True)))
        temp.replace(r/'source-stopped.json')
        raise SystemExit(1)
    secret+=byte
assert secret==b'fixture-password\n'
print(secret.decode(),flush=True) # Deliberate echo: launcher must NEVER forward/log it.
(r/'source-ready.json').write_text('{}')
stop=sys.stdin.buffer.read(1)
m=json.loads((r/'live_status.json').read_text())['monitor']
temp=r/'source-stopped.temp'
temp.write_text(json.dumps(dict(ordered=m['sample_close'] is not None,byte=list(stop))))
temp.replace(r/'source-stopped.json')
time.sleep(.4) # Controller may exit before the local terminal process.
''', encoding='utf-8')
            source_processes=[]
            def source_start(*_):
                process=subprocess.Popen([sys.executable,'-X','utf8','-E','-B',str(child),str(run)],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                    creationflags=demo.process_flags())
                source_processes.append(process)
                return process
            stop=threading.Event()
            messages=[]
            def say(value):
                messages.append(value)
                if value.startswith('演示界面已就绪'):
                    if fail_after_ready:
                        raise RuntimeError('synthetic UI/output failure')
                    stop.set()
            try:
                with patch.object(demo,'ROOT',root),patch.object(demo,'NEXYS',nexys), \
                        patch.object(demo,'local_check',return_value={'passed':True}), \
                        patch.object(demo,'prepare',return_value=launch), \
                        patch.object(demo,'start_source',side_effect=source_start) as starter, \
                        patch.object(demo,'read_password',return_value='fixture-password'):
                    code=demo.run_demo(True,stop,say=say)
                reports=list(folder.parent.glob('*_launcher/result.json'))
                self.assertEqual(len(reports),1)
                report=demo.read_json(reports[0])
                self.assertFalse(report['formal_release'])
                self.assertFalse(report['physical_recovery_verified'])
                self.assertNotIn('fixture-password','\n'.join(messages))
                for path in reports[0].parent.iterdir():
                    if path.is_file():
                        self.assertNotIn(b'fixture-password',path.read_bytes())
                if pre_source_failure:
                    starter.assert_not_called()
                    self.assertEqual(code,1)
                    self.assertFalse(report['interactive_seen'])
                else:
                    self.assertTrue((run/'source-stopped.json').is_file(),json.dumps(report,ensure_ascii=False))
                    receipt=demo.read_json(run/'source-stopped.json')
                    self.assertEqual(receipt,dict(ordered=True,byte=[3]),json.dumps(report,ensure_ascii=False))
                    self.assertTrue(report['recovery_verified'], json.dumps(report, ensure_ascii=False)+'\n'+
                                    (reports[0].parent/'controller_stderr.txt').read_text(errors='replace'))
                    self.assertEqual(code,1 if fail_after_ready else 0)
                    self.assertEqual(report['successful_demo'],not fail_after_ready)
                demo.prior_session_check(root/'board/preparation/demo_launcher_current.json',root)
            finally:
                for process in source_processes:
                    if process.poll() is None:
                        process.kill() # Local fixture only; no SSH/hardware process.
                        process.wait(timeout=5)

    def test_full_start_password_display_close_stop_recovery(self):
        self.exercise()

    def test_camera_campaign_automatically_closes_before_source_stop(self):
        self.exercise(campaign=True)

    def test_exception_after_start_still_closes_before_stopping(self):
        self.exercise(fail_after_ready=True)

    def test_dp_not_ready_never_starts_source(self):
        self.exercise(pre_source_failure=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
