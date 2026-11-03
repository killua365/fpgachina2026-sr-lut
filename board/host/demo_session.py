"""One visible terminal around the unchanged r8/r10 supervised session.

No hardware work is performed on import or by --check. --ready is a fresh
operator assertion, not an automated observation of the external screen.
Only the original controller owns JTAG/serial/recovery. The source terminal
is piped so Ctrl+C reaches this coordinator first, not the remote DMA owner.
Raw source-terminal output and passwords are never written to a log.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'board/pynq'
NEXYS = ROOT / 'board/host'
TOOLS = ROOT / 'build/artifacts/session'
LAUNCHER_FILES = ('demo.cmd', 'board/host/demo_session.py', 'sim/software/host/test_demo_session.py',
                  'board/host/qualify_demo_session.py', 'tools/python.cmd', 'tools/initialize_runtime.ps1',
                  'tools/runtime_paths.py', 'runtime/environments.json',
                  'runtime/host_python_3_12_14.json', 'runtime/reference_python_3_12_14.json')
LAUNCHER_MANIFEST = 'board/release/launcher.json'
sys.path[:0] = [str(RUNTIME), str(NEXYS), str(ROOT/'tools')]
from runtime_paths import runtime_python

from bridge_ordered_protocol import receive, send
from business_session_policy import OPERATOR_LIMITS
from prepare_business_session import prepare, source_start_check


def read_json(path):
    # Windows can briefly deny an open while the controller atomically replaces
    # a status file. Retry fresh reads only; never substitute a stale snapshot.
    for attempt in range(6):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8-sig'))
        except OSError as error:
            if (attempt == 5 or (not isinstance(error, (PermissionError, FileNotFoundError))
                    and getattr(error, 'winerror', None) not in (5, 32, 33))):
                raise
            time.sleep(.05)


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def baseline(launch, confirmed):
    if confirmed is not True:
        raise ValueError('A fresh operator confirmation is required')
    return dict(recorded_host_utc=datetime.now(timezone.utc).isoformat(),
                authority='user', run_id=launch['run_id'],
                original_pattern_normal=True, osd='1280x1024@60',
                onsite_ready=True, cables_verified=True,
                statement='Operator confirmed the original pattern, 1280x1024@60 OSD, '
                          'existing cables/peripherals and availability for recovery at launch.',
                confirmation_method='demo launcher --ready or explicit console YES')


class SessionLock:
    """OS lock plus a persistent pointer: a crashed launcher cannot hide a live run."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        import msvcrt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        if self.path.stat().st_size == 0:
            self.stream.write(b' ')
            self.stream.flush()
        self.stream.seek(0)
        try:
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.stream.close()
            self.stream = None
            raise RuntimeError('另一个演示启动器仍在运行，请先在原终端结束。') from None
        return self

    def __exit__(self, *_):
        import msvcrt
        if self.stream is not None:
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()


class ConsoleInput:
    """Consume Ctrl+C locally, so cmd/PowerShell cannot interrupt the child first."""
    def __enter__(self):
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        self.kernel.GetStdHandle.restype = wintypes.HANDLE
        self.kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.handle = self.kernel.GetStdHandle(wintypes.DWORD(-10))
        self.original = wintypes.DWORD()
        if not self.kernel.GetConsoleMode(self.handle, ctypes.byref(self.original)):
            raise OSError('需要可交互的 Windows 控制台，请从 PowerShell 执行 demo.cmd。')
        # ENABLE_PROCESSED_INPUT causes system-wide Ctrl+C delivery to console
        # processes. Reading it as input avoids a batch-termination prompt too.
        if not self.kernel.SetConsoleMode(self.handle, self.original.value & ~1):
            raise ctypes.WinError(ctypes.get_last_error())
        self.special = False
        return self

    def interrupted(self):
        import msvcrt
        interrupted = False
        while msvcrt.kbhit():
            char = msvcrt.getwch()
            if self.special:
                self.special = False
            elif char in ('\x00', '\xe0'):
                self.special = True
            elif char == '\x03':
                interrupted = True
        return interrupted

    def __exit__(self, *_):
        self.kernel.SetConsoleMode(self.handle, self.original.value)


def prior_session_check(pointer, root=ROOT):
    if not pointer.exists():
        return
    previous = read_json(pointer)
    run = Path(previous['run']).resolve()
    run.relative_to((root / 'board/acceptance').resolve())
    if previous.get('controller_started') is False:
        return
    result = read_json(run / 'result.json') if (run / 'result.json').is_file() else {}
    source_safe = result.get('source_stop_verified') is True or previous.get('source_launch_attempted') is False
    if (result.get('run_id') != previous['run_id'] or result.get('state') != 'ended' or
            result.get('configuration_recovery_verified') is not True or not source_safe):
        raise RuntimeError('上次演示尚未确认完整恢复，请保留原终端与记录并先处理：' + str(run))


def replace_json(path, value):
    # The single launcher lock is held; retain the old pointer until replacement.
    temporary = path.with_suffix('.new')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    for attempt in range(5):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.05)


def read_session(run, run_id):
    result = read_json(run / 'result.json') if (run / 'result.json').is_file() else {}
    if result and result.get('run_id') != run_id:
        raise ValueError('Foreign controller result')
    live = read_json(run / 'live_status.json') if (run / 'live_status.json').is_file() else {}
    return result, live


def request_end(run, run_id, operation):
    plan, result = read_json(run / 'plan.json'), read_json(run / 'result.json')
    if plan.get('run_id') != run_id or result.get('run_id') != run_id:
        raise ValueError('Foreign session end request')
    if result.get('state') == 'ended':
        return False
    path = run / (operation + '.request')
    if path.exists():
        if not receive(path, run_id, operation):
            raise ValueError('Invalid existing request')
        return False
    send(path, run_id, operation)
    return True


class DemoPolicy:
    """Only orchestration; the qualified controller remains the safety authority."""
    def __init__(self):
        self.source_started = False
        self.requested = False
        self.interrupted = False

    def actions(self, *, result, live, stop=False, ready=False,
                controller_exit=None, source_exit=None):
        monitor = live.get('monitor', {})
        state = monitor.get('state')
        actions = []
        ended = result.get('state') == 'ended'
        stopping = state in ('closing', 'stopping_source', 'restoring', 'done')
        if (stop or (self.source_started and source_exit is not None)) and not self.requested:
            if result and not ended and not stopping:
                actions.append('close' if state == 'sampling' else 'cancel')
        if (not self.source_started and ready and not stop and not stopping and
                not monitor.get('failures') and controller_exit is None and not ended):
            actions.append('start_source')
        # A failed close acknowledgement is explicitly a failure in the original
        # monitor. Stop the source then too, but never claim a successful order.
        stop_authorized = (state == 'stopping_source' and
                           (monitor.get('sample_close') is not None or bool(monitor.get('failures'))))
        if self.source_started and source_exit is None and not self.interrupted:
            if stop_authorized:
                actions.append('interrupt_source')
            elif controller_exit is not None and not (ended and result.get('source_stop_verified') is True):
                actions.extend(('controller_lost', 'interrupt_source'))
        return actions


class SourceConsole:
    """Piped SSH console. Never forward arbitrary output or persist credentials."""
    PROMPT = re.compile(rb'\[sudo\][^\r\n]{0,160}:\s*$')
    ERRORS = {
        b'Permission denied': 'SSH 认证失败',
        b'Connection reset': 'SSH 连接被重置',
        b'Connection timed out': 'SSH 连接超时',
        b'Remote preflight failed': 'PYNQ 包预检失败',
        b'Nexys-first session is not ready': 'Nexys 当前未就绪',
        b'Nexys session ended or failed during source preflight': '预检期间会话已结束',
    }

    def __init__(self, process):
        self.process = process
        self.chunks = queue.Queue(maxsize=32)
        self.done = threading.Event()
        self.tail = b''
        self.prompts = 0
        self.error_labels = set()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while not self.done.is_set():
                chunk = self.process.stdout.read1(4096)
                if not chunk:
                    break
                while not self.done.is_set():
                    try:
                        self.chunks.put(chunk, timeout=.1)
                        break
                    except queue.Full:
                        continue
        finally:
            self.done.set()

    def poll_prompt(self):
        prompt = False
        while True:
            try:
                chunk = self.chunks.get_nowait()
            except queue.Empty:
                break
            self.tail = (self.tail + chunk)[-4096:]
            for marker, label in self.ERRORS.items():
                if marker in self.tail:
                    self.error_labels.add(label)
            if self.PROMPT.search(self.tail):
                self.prompts += 1
                prompt = True
                self.tail = b''
        return prompt

    def password(self, value):
        if self.process.poll() is not None:
            raise RuntimeError('Source exited before sudo input')
        if '\n' in value or '\r' in value:
            raise ValueError('A password must be one line')
        try:
            encoded = value.encode('utf-8') + b'\n'
        except UnicodeError:
            raise ValueError('Password cannot be encoded for the remote terminal') from None
        self.process.stdin.write(encoded)
        self.process.stdin.flush()

    def interrupt(self):
        if self.process.poll() is None:
            self.process.stdin.write(b'\x03')
            self.process.stdin.flush()

    def close(self):
        self.done.set()
        self.reader.join(timeout=2)
        if self.process.poll() is not None:
            self.process.stdin.close()
            self.process.stdout.close()


def process_flags():
    return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


def powershell_path():
    return Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def start_source(launch, run):
    # Reuse all original local/remote checks and the .started anti-reentry file.
    command = [str(powershell_path()), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
               str(RUNTIME / 'run_business_session.ps1'), '-Launch', str(launch),
               '-Session', str(run), '-PythonPath', sys.executable, '-Run']
    return subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, creationflags=process_flags())


def read_password(stop, active, *, keyboard=None, sleep=time.sleep, output=print):
    """No echo, no log, and no blocking input after the controller has closed."""
    if keyboard is None:
        import msvcrt
        keyboard = msvcrt
    output('PYNQ sudo 密码（不显示、不保存）：', end='', flush=True)
    chars = []
    special = False
    try:
        while not stop.is_set() and active():
            if not keyboard.kbhit():
                sleep(.05)
                continue
            char = keyboard.getwch()
            if special:
                special = False
            elif char in ('\x00', '\xe0'):
                special = True
            elif char in ('\x03', '\x1b', '\x1a'):
                stop.set()
                return None
            elif char in ('\r', '\n'):
                return ''.join(chars)
            elif char == '\b':
                if chars:
                    chars.pop()
            elif char == '\x15':
                chars.clear()
            elif char.isprintable():
                chars.append(char)
        return None
    finally:
        chars.clear()
        output('', flush=True)


def recover_after_error(controller, source, console, run, run_id, report, say):
    """Keep supervising an exception path until the original bounded recovery ends."""
    policy = DemoPolicy()
    policy.source_started = source is not None
    deadline = time.monotonic() + sum(OPERATOR_LIMITS[k] for k in ('load', 'warmup', 'source_stop', 'recovery')) + 60
    while True:
        try:
            result, live = read_session(run, run_id)
            for action in policy.actions(result=result, live=live, stop=True,
                    controller_exit=controller.poll(), source_exit=source.poll() if source else None):
                if action in ('close', 'cancel'):
                    request_end(run, run_id, action)
                    policy.requested = True
                elif action == 'interrupt_source':
                    console.interrupt()
                    policy.interrupted = True
                elif action == 'controller_lost':
                    report['errors'].append('controller_lost_during_exception_recovery')
            if console:
                console.poll_prompt()  # Drain pipes, but never request another password while closing.
            if controller.poll() is not None and (source is None or source.poll() is not None):
                return
        except (OSError, ValueError, KeyError) as error:
            label = 'exception_recovery: ' + type(error).__name__
            if label not in report['errors']:
                report['errors'].append(label)
        if time.monotonic() >= deadline:
            report['errors'].append('exception_recovery_deadline_unknown')
            say('恢复超过时限，保留进程与记录；不能确认硬件已停稳。')
            return
        time.sleep(.1)


def local_check():
    if os.name != 'nt':
        raise RuntimeError('This launcher requires the configured Windows host')
    if Path(sys.executable).resolve() != runtime_python('host').resolve():
        raise RuntimeError('Use demo.cmd with the verified project Python')
    verify_launcher(ROOT)
    from business_session_gate import check
    return check(TOOLS)


def verify_launcher(root):
    """Bind this convenience layer to its own offline evidence, not r10's old tests."""
    root = Path(root).resolve()
    manifest = read_json(root / LAUNCHER_MANIFEST)
    records = manifest['files']
    if len(records) != len(LAUNCHER_FILES) or {item['path'] for item in records} != set(LAUNCHER_FILES):
        raise ValueError('Incomplete demo launcher inventory')
    expected = {}
    for item in records:
        original_path = root / item['path']
        path = original_path.resolve()
        path.relative_to(root)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if original_path.is_symlink() or actual != item['sha256']:
            raise ValueError('Demo launcher changed: ' + item['path'])
        expected[item['path']] = actual
    qualification = (root / manifest['qualification']).resolve()
    qualification.relative_to(root / 'report/validation')
    if hashlib.sha256(qualification.read_bytes()).hexdigest() != manifest['qualification_sha256']:
        raise ValueError('Demo launcher qualification changed')
    result = read_json(qualification)
    if (result.get('passed') is not True or result.get('hardware_access') is not False or
            result.get('tests', 0) < 23 or any(result.get(key) != 0 for key in ('failures', 'errors', 'skipped')) or
            result.get('inputs') != expected):
        raise ValueError('Demo launcher offline qualification incomplete')
    tools_manifest = root / TOOLS.relative_to(ROOT) / 'manifest.json'
    if hashlib.sha256(tools_manifest.read_bytes()).hexdigest() != manifest['session_tool_manifest_sha256']:
        raise ValueError('Demo launcher is bound to a different controller')
    return True


def run_demo(confirmed, stop, say=print, interrupt_requested=lambda: False):
    pointer = ROOT / 'board/preparation/demo_launcher_current.json'
    with SessionLock(ROOT / 'build/locks/demo_launcher.lock'):
        prior_session_check(pointer)
        say('正在初始化演示，请稍候……')
        local_check()
        if stop.is_set() or interrupt_requested():
            return 1
        launch_path = prepare()
        launch = read_json(launch_path)
        folder = launch_path.parent.parent
        run = folder / ('n5_demo_' + launch['run_id'])
        launcher = folder / ('n5_demo_' + launch['run_id'] + '_launcher')
        launcher.mkdir(exist_ok=False)
        baseline_path = launcher / 'baseline.json'
        write_new(baseline_path, baseline(launch, confirmed))
        pointer.parent.mkdir(parents=True, exist_ok=True)
        current = dict(run=str(run), run_id=launch['run_id'], launcher=str(launcher),
                       launch=str(launch_path), controller_started=False, source_launch_attempted=False)
        replace_json(pointer, current)
        report = dict(schema='single_terminal_demo_v1', run_id=launch['run_id'],
                      run=str(run), launch=str(launch_path), source_started=False,
                      interactive_seen=False, errors=[], physical_recovery_verified=False,
                      n5_passed=False, n6_passed=False, formal_release=False)
        source = console = controller = None
        policy = DemoPolicy()
        started = time.monotonic()
        last_phase = None
        result, live = {}, {}
        close_time = None
        try:
            with (launcher / 'controller_stdout.txt').open('xb') as output, \
                    (launcher / 'controller_stderr.txt').open('xb') as errors:
                command = [sys.executable, '-X', 'utf8', '-E', '-B',
                           str(NEXYS / 'supervise_business_session.py'), str(TOOLS), str(run),
                           '--launch', str(launch_path), '--baseline', str(baseline_path),
                           '--case', 'business', '--source-stop-mode', 'operator']
                # Persist a conservative marker BEFORE spawn, including the tiny
                # crash window before the controller has created its result file.
                current['controller_started'] = True
                replace_json(pointer, current)
                controller = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=errors, creationflags=process_flags())
                report['controller_pid'] = controller.pid
                say('正在启动 Nexys；就绪后自动启动 PYNQ。请保持本终端打开。')
                say('本轮记录：' + str(launcher))
                while True:
                    if interrupt_requested():
                        stop.set()
                    result, live = read_session(run, launch['run_id'])
                    controller_exit = controller.poll()
                    source_exit = source.poll() if source else None
                    ready = False
                    if source is None and not stop.is_set() and controller_exit is None:
                        try:
                            ready = source_start_check(run, launch)
                        except (OSError, ValueError, KeyError):
                            pass
                    for action in policy.actions(result=result, live=live, stop=stop.is_set(),
                            ready=ready, controller_exit=controller_exit, source_exit=source_exit):
                        if action in ('close', 'cancel'):
                            request_end(run, launch['run_id'], action)
                            policy.requested = True
                            say('正在结束演示并恢复原配置，请等待完成……')
                        elif action == 'start_source':
                            current['source_launch_attempted'] = True
                            replace_json(pointer, current)
                            source = start_source(launch_path, run)
                            console = SourceConsole(source)
                            policy.source_started = report['source_started'] = True
                            report['source_pid'] = source.pid
                            say('正在启动 PYNQ；如需 sudo，密码提示将在本终端出现。')
                        elif action == 'controller_lost':
                            report['errors'].append('controller_ended_before_source_exit')
                            say('控制器已异常结束，正在请求源停止；恢复结果尚未确认。')
                        elif action == 'interrupt_source':
                            console.interrupt()
                            policy.interrupted = True
                            report['source_interrupt_after_sample_close'] = live.get('monitor', {}).get('sample_close') is not None
                            close_time = time.monotonic()
                    if console is not None and console.poll_prompt() and not stop.is_set():
                        if console.prompts > 3:
                            report['errors'].append('too_many_sudo_prompts')
                            stop.set()
                        else:
                            def password_allowed():
                                if controller.poll() is not None or source.poll() is not None:
                                    return False
                                _, state = read_session(run, launch['run_id'])
                                monitor = state.get('monitor', {})
                                return (not monitor.get('failures') and
                                        monitor.get('state') in ('warming', 'starting', 'sampling') and
                                        monitor.get('sample_close') is None)
                            secret = read_password(stop, password_allowed)
                            if secret is None:
                                stop.set()
                            else:
                                if not stop.is_set():
                                    console.password(secret)
                            del secret
                    if source is not None:
                        snapshots = [run/'source_latest.json'] if (run/'source_latest.json').exists() else sorted(run.glob('source_[0-9]*.json'))
                        if snapshots:
                            snapshot = read_json(snapshots[-1])
                            if snapshot.get('run_id') != launch['run_id']:
                                raise ValueError('Foreign source snapshot')
                            phase = snapshot.get('worker', {}).get('phase')
                            monitor = live.get('monitor', {})
                            if phase != last_phase:
                                if phase == 'initializing_demo':
                                    say('正在初始化演示')
                                if phase in ('pixel_smoke_x2', 'pixel_smoke_x4', 'video_golden_x2', 'video_golden_x4'):
                                    say('正在初始化并检查像素：' + phase)
                                last_phase = phase
                            if phase == 'camera_performance_complete' and snapshot.get('state') == 'running':
                                report['camera_campaign_complete'] = True
                                stop.set()
                            if (not report['interactive_seen'] and phase in ('interactive_business', 'camera_performance_running', 'camera_performance_complete') and
                                    snapshot.get('state') == 'running' and
                                    monitor.get('state') == 'sampling' and not monitor.get('failures') and
                                    monitor.get('sample_close') is None):
                                report['interactive_seen'] = True
                                if phase.startswith('camera_performance'):
                                    say('摄像头性能测试正在自动执行，完成后会自动恢复。请暂时不要操作界面。')
                                else:
                                    say('演示界面已就绪，请操作 DELL 上的鼠标和 PYNQ 按钮。')
                                    say('演示持续运行，Ctrl+C 退出并自动收尾。')
                    if controller_exit is not None and (source is None or source.poll() is not None):
                        break
                    if controller_exit is not None and close_time is None:
                        close_time = time.monotonic()
                    # Never kill a DMA owner or manufacture recovery on a timeout.
                    if launch.get('end_mode') != 'demo' and time.monotonic() - started > OPERATOR_LIMITS['total'] + 60:
                        report['errors'].append('launcher_recovery_deadline_unknown')
                        break
                    if controller_exit is not None and close_time is not None and time.monotonic() - close_time > OPERATOR_LIMITS['source_stop']:
                        report['errors'].append('source_exit_unverified')
                        break
                    time.sleep(.1)
        except Exception as exc:
            report['errors'].append(type(exc).__name__ + ': ' + str(exc))
            try:
                write_new(launcher / 'exception.json', dict(error_type=type(exc).__name__,
                    message=str(exc), run_id=launch['run_id']))
            except OSError:
                pass
            say('启动器遇到异常，正在通过原控制器完成有界恢复，请保持终端打开。')
            if controller is not None:
                recover_after_error(controller, source, console, run, launch['run_id'], report, say)
        finally:
            if console is not None:
                console.poll_prompt()
                report['source_error_categories'] = sorted(console.error_labels)
                console.close()
            report['controller_exit'] = controller.poll() if controller else None
            report['source_exit'] = source.poll() if source else None
            try:
                result, live = read_session(run, launch['run_id'])
            except (OSError, ValueError, KeyError):
                result = {}
            report['controller_result'] = result
            report['source_stop_verified'] = result.get('source_stop_verified') is True
            report['configuration_recovery_verified'] = result.get('configuration_recovery_verified') is True
            report['recovery_verified'] = (result.get('state') == 'ended' and
                report['source_stop_verified'] and report['configuration_recovery_verified'] and
                (source is None or source.poll() is not None))
            report['successful_demo'] = (report['interactive_seen'] and report['recovery_verified'] and
                not report['errors'] and not result.get('monitor', {}).get('failures') and
                report['source_exit'] == 0)
            write_new(launcher / 'result.json', report)
        if report['recovery_verified']:
            say('源已停止，原服务及 Nexys 原配置已恢复。请确认屏幕回到原色块。')
        else:
            say('恢复尚未完整确认，请保留记录并处理，不要重复启动：' + str(launcher))
        reasons = report['errors'] + report.get('source_error_categories', []) + result.get('monitor', {}).get('failures', [])
        if reasons:
            say('本轮问题：' + '; '.join(reasons))
        return 0 if report['successful_demo'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='在一个终端启动 SR-LUT 双板演示，并自动检查和收尾。')
    parser.add_argument('--ready', action='store_true', help='确认本次原色块/1280x1024@60、既定接线及外设正常，可以配合恢复')
    parser.add_argument('--check', action='store_true', help='只检查本地版本；不 SSH、不加载、不生成会话')
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(local_check(), ensure_ascii=False))
        return 0
    if not sys.stdin.isatty():
        raise RuntimeError('请在可输入密码的本机终端执行 demo.cmd，不要重定向输入。')
    confirmed = args.ready
    if not confirmed:
        print('请确认：原色块和 1280×1024@60Hz 正常，既定接线、已供电集线器及鼠标/摄像头就绪。')
        confirmed = input('确认本次现场正常并可以配合结束恢复，输入 YES：').strip() == 'YES'
    if not confirmed:
        print('已取消，未连接板卡。')
        return 1
    stop = threading.Event()
    def interrupt(*_):
        stop.set()
    previous = signal.signal(signal.SIGINT, interrupt)
    previous_break = signal.signal(signal.SIGBREAK, interrupt) if hasattr(signal, 'SIGBREAK') else None
    try:
        with ConsoleInput() as console:
            return run_demo(confirmed, stop, say=lambda value: print(value, flush=True),
                            interrupt_requested=console.interrupted)
    finally:
        signal.signal(signal.SIGINT, previous)
        if previous_break is not None:
            signal.signal(signal.SIGBREAK, previous_break)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print('演示未启动或未完成：' + str(error), file=sys.stderr)
        raise SystemExit(1)
