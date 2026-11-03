"""Independent VGA test or unlimited demo, with ordered source/FPGA cleanup.

This has its own candidate identity and records. It never asserts the ordinary
demo's color-bar baseline and never updates the default release or writes Flash.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path[:0]=[str(ROOT/'board/host'),str(ROOT/'board/pynq')]
from bridge_ordered_protocol import send, receive
from business_session_source import Source
from business_session_remote import BOARD_PYTHON
from capture_ftdi_uart import Ftdi
from decode_business_diagnostic import decode, FRAME_BYTES
from demo_session import SourceConsole, SessionLock, replace_json
from bounded_logs import BoundedLog
from longrun_lease import refresh
from longrun_process import OwnedProcess, powershell, qpc_ns
from prepare_business_session import prepare
from qualify_candidate import check

BIN=Path(os.environ.get('SR_LUT_VIVADO_BIN', r'D:\vivado2026.1\2026.1\Vivado\bin'))


def utc():
    return datetime.now(timezone.utc).isoformat()


def live_status(raw):
    parsed=decode(bytes(raw[-FRAME_BYTES*45:]))
    rows=parsed['frames']
    if not rows:
        return dict(available=False)
    errors=[e for e in parsed['errors'] if e['error']!='truncated']
    if errors or any(b['sequence']!=(a['sequence']+1)&0xffffffff for a,b in zip(rows,rows[1:])):
        return dict(available=False,transport_fault=True)
    w=rows[-1]['words']
    if w[0]!=0x26102101 or w[1]!=0x4e344231 or w[40]!=0x53524944:
        return dict(available=False,identity_fault=True)
    pages={}
    for r in rows:
        words=r['words']
        pages[str(words[36]&255)]=f'{words[38]:08x}{words[37]:08x}'
    return dict(available=True,sequence=rows[-1]['sequence'],error_flags=w[2],
        root_seconds=(w[30]+(w[31]<<32))/1e8,clock_memory_status=w[35],
        link_ready=bool(w[39]&1),source_present=bool(w[18]&1),
        geometry=w[27:30],written=w[4],scanned=w[6],rx_good=w[25],rx_bad=w[26],
        pages=pages)


def source_command(launch):
    remote=launch['remote_package']
    command=['sudo','-H','env','XILINX_XRT=/usr',BOARD_PYTHON,'-B',
        remote+'/board/pynq/hdmi_business_app.py','run','--root',remote,
        '--destination',launch['remote_results'],'--run-id',launch['run_id'],'--end-mode','demo']
    return ['ssh.exe','-i',str(Path.home()/'.ssh/sr_lut_pynq_z2_ed25519'),
        '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8',
        '-o','ServerAliveInterval=3','-o','ServerAliveCountMax=2','-tt',
        'xilinx@192.168.2.99',shlex.join(command)]


def session_limits(demo):
    return dict(loader_mode='demo' if demo else 'timed',
                showing_seconds=None if demo else 300,
                total_seconds=None if demo else 1050)


def expired(seconds, elapsed):
    return seconds is not None and elapsed > seconds


def run(password, *, demo=False, stop_requested=lambda: False):
    limits=session_limits(demo)
    qualification=check()
    launch_path=prepare(end_mode='demo')
    launch=json.loads(launch_path.read_text())
    run_id=launch['run_id']
    out=ROOT/'board/analysis'/('vga_candidate_26102101_'+run_id)
    out.mkdir(parents=True,exist_ok=False)
    control=out/'control';control.mkdir()
    board=out/'board'
    inventory=out/'inventory.json'
    subprocess.run([powershell(),'-NoProfile','-NonInteractive','-File',
        str(ROOT/'board/host/longrun_inventory.ps1'),'-Destination',str(inventory)],check=True,timeout=30)
    source=Source(launch,persistent=True)
    initial=source.query()
    if initial['state']!='absent':
        source.close()
        raise RuntimeError('Another PYNQ business worker already exists')
    result=dict(schema='nexys_vga_demo_v1' if demo else 'nexys_vga_test_v1',candidate_id='26102101',run_id=run_id,
        started_host_utc=utc(),state='running',baseline='Current VGA connection; no new visual baseline asserted',
        end_mode='demo' if demo else 'timed',limits=limits,
        qualification=qualification,source_started=False,source_stop_verified=True,
        configuration_recovery_verified=False,physical_display_verified=False,
        default_images_replaced=False,flash_written=False)
    replace_json(out/'result.json',result)
    replace_json(out/'launch.json',launch)
    print('VGA_TEST_DIRECTORY '+str(out),flush=True)
    if demo:
        replace_json(ROOT/'board/analysis/vga_demo_current.json',dict(run_id=run_id,directory=str(out)))
        print('正在初始化 VGA 演示；持续运行，Ctrl+C 有序退出。',flush=True)
    server=loader=console=source_process=None
    device=Ftdi();raw=bytearray();buffer=ctypes.create_string_buffer(65536)
    pool=ThreadPoolExecutor(max_workers=1)
    future=None;source_snapshot=initial;next_query=0
    phase='waiting_link';phase_at=time.monotonic();started=phase_at
    active_at=None;stable_at=None;absent_at=None;last_save=0
    interrupted=False;last_live=dict(available=False)
    failures=[];events=[]

    def event(name,**values):
        row=dict(event=name,host_utc=utc(),**values)
        events.append(row)
        print(json.dumps(row),flush=True)
        replace_json(out/'events.json',events)

    def transition(name):
        nonlocal phase,phase_at
        phase=name;phase_at=time.monotonic();event('phase',phase=phase)

    def close_request():
        if not (control/'close.request').exists():send(control/'close.request',run_id,'close')

    try:
        result['uart']=device.open()
        server=OwnedProcess(out/'server',BIN/'unwrapped/win64.o/hw_server.exe',['-s','tcp::3121'],out)
        refresh(control,qpc_ns())
        loader=OwnedProcess(out/'loader',BIN/'vivado.bat',[
            '-mode','batch','-notrace','-source',HERE/'load_candidate.tcl',
            '-log',out/'loader.log','-journal',out/'loader.jou','-tclargs',
            sys.executable,HERE,board,control,HERE,run_id,limits['loader_mode']],out)
        with (BoundedLog(out/'uart.bin',binary=True) if demo else (out/'uart.bin').open('xb',buffering=0)) as capture:
            while loader.poll() is None:
                now=time.monotonic()
                refresh(control,qpc_ns())
                count,errors,queued=device.read(buffer)
                if count:
                    block=buffer.raw[:count];raw.extend(block);capture.write(block)
                    if demo:
                        del raw[:-FRAME_BYTES*90]
                        capture.flush()
                active=(board/'active.txt').exists() and not (board/'recovery_started_qpc.txt').exists()
                if active:
                    if active_at is None:active_at=now;event('candidate_loaded')
                    last_live=live_status(raw)
                if console is not None:
                    if console.poll_prompt():
                        if console.prompts>1:raise RuntimeError('Sudo authentication was rejected')
                        console.password(password)
                    if console.error_labels and phase not in ('closing','source_stopping','restoring'):
                        failures.extend(sorted(console.error_labels));transition('closing')
                if future is not None and future.done():
                    try:
                        source_snapshot=future.result()
                        replace_json(out/'source_latest.json',source_snapshot)
                    except Exception as exc:
                        failures.append('Source query: '+str(exc))
                        if phase not in ('closing','source_stopping','restoring'):transition('closing')
                    future=None
                if result['source_started'] and future is None and now>=next_query and source_snapshot['state']!='finished':
                    future=pool.submit(source.query);next_query=now+1
                healthy=(last_live.get('available') and last_live.get('link_ready') and
                    not last_live.get('error_flags') and last_live.get('clock_memory_status')==0x3b)
                if stop_requested() and phase not in ('closing','source_stopping','restoring'):
                    event('manual_stop_requested');transition('closing')
                if phase=='waiting_link':
                    if healthy:
                        if stable_at is None:stable_at=now
                        if now-stable_at>=2:
                            event('link_ready',input=last_live)
                            source_process=subprocess.Popen(source_command(launch),cwd=ROOT,
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP)
                            console=SourceConsole(source_process)
                            result.update(source_started=True,source_stop_verified=False)
                            transition('source_starting')
                    else:stable_at=None
                    if (active_at is not None and now-active_at>60) or now-started>210:
                        failures.append('Candidate could not establish a stable display link');transition('closing')
                elif phase=='source_starting':
                    if source_snapshot['state']=='running' and source_snapshot.get('worker',{}).get('phase')=='interactive_business' and healthy and last_live.get('rx_good',0)>10:
                        result['digital_video_path_verified']=True
                        transition('showing');event('DEMO_READY' if demo else 'PHYSICAL_DISPLAY_CHECK_READY',input=last_live)
                    elif now-phase_at>240:
                        failures.append('PYNQ UI/video path did not become ready');transition('closing')
                elif phase=='showing':
                    if not healthy:
                        failures.append('Display link lost during UI');transition('closing')
                    elif (out/'finish.request').exists():
                        request=json.loads((out/'finish.request').read_text())
                        if request.get('run_id')!=run_id:raise ValueError('Foreign finish request')
                        result['physical_observation']=request
                        result['physical_display_verified']=request.get('display_normal') is True
                        transition('closing')
                    elif expired(limits['showing_seconds'],now-phase_at):
                        failures.append('Physical display observation not received within 300 seconds');transition('closing')
                if phase=='closing':
                    close_request()
                    if receive(board/'close.ack',run_id,'close'):
                        transition('source_stopping')
                if phase=='source_stopping':
                    if console is not None and not interrupted:
                        console.interrupt();interrupted=True;event('source_interrupt_sent')
                    stopped=not result['source_started'] or source_snapshot['state']=='finished'
                    if stopped:
                        result['source_stop_verified']=True
                        input_absent=last_live.get('available') and not last_live.get('source_present')
                        if input_absent:
                            if absent_at is None:absent_at=now
                            if now-absent_at>=2:
                                send(control/'restore.request',run_id,'restore');transition('restoring')
                        else:absent_at=None
                    elif now-phase_at>300:
                        raise RuntimeError('PYNQ ordered shutdown not verified within 300 seconds')
                if phase=='restoring' and now-phase_at>120:
                    raise RuntimeError('Nexys restoration timeout')
                if now-last_save>=1:
                    last_save=now
                    replace_json(out/'live_status.json',dict(run_id=run_id,phase=phase,input=last_live,
                        source_state=source_snapshot['state'],elapsed_seconds=now-started))
                    replace_json(out/'result.json',result)
                if expired(limits['total_seconds'],now-started):raise TimeoutError('Independent VGA experiment deadline')
                time.sleep(.05)
    except BaseException as exc:
        failures.append(type(exc).__name__+': '+str(exc))
        # Continue heartbeat while the existing supervisor closes DMA/services.
        if loader is not None and loader.poll() is None:
            until=time.monotonic()+320
            close_request()
            while time.monotonic()<until and loader.poll() is None:
                refresh(control,qpc_ns())
                if console is not None:
                    console.poll_prompt()
                    if not interrupted:console.interrupt();interrupted=True
                if future is not None and future.done():
                    try:source_snapshot=future.result()
                    except Exception as error:failures.append(str(error))
                    future=None
                if result['source_started'] and future is None and source_snapshot['state']!='finished':
                    future=pool.submit(source.query)
                if (not result['source_started'] or source_snapshot['state']=='finished') and receive(board/'close.ack',run_id,'close'):
                    result['source_stop_verified']=True
                    if not (control/'restore.request').exists():send(control/'restore.request',run_id,'restore')
                time.sleep(.1)
    finally:
        # A loader error must not leave the independently supervised PYNQ UI
        # running. Keep the existing PID/session verification during shutdown.
        if result['source_started'] and not result['source_stop_verified']:
            if console is not None:console.interrupt()
            until=time.monotonic()+300
            while time.monotonic()<until and source_snapshot['state']!='finished':
                if loader is not None and loader.poll() is None:refresh(control,qpc_ns())
                if future is not None and future.done():
                    try:source_snapshot=future.result()
                    except Exception as exc:failures.append(str(exc))
                    future=None
                if future is None and source_snapshot['state']!='finished':future=pool.submit(source.query)
                time.sleep(.1)
            result['source_stop_verified']=source_snapshot['state']=='finished'
        password=None
        if loader is not None:
            result['loader_exit']=loader.poll()
            if (board/'operation.txt').exists():
                operation=(board/'operation.txt').read_text()
                result['loader_operation']=operation
                result['configuration_recovery_verified']='recovery_exit=0' in operation and 'loaded=1' in operation
        try:
            device.close()
            result['uart_closed_and_latency_restored']=True
        except Exception as exc:failures.append('UART cleanup: '+str(exc))
        if console is not None:console.close()
        if future is not None:
            try:future.result(timeout=15)
            except Exception as exc:failures.append(str(exc))
        pool.shutdown(wait=True)
        source.close()
        for owned in (loader,server):
            if owned is not None:owned.close()
        result.update(state='ended',ended_host_utc=utc(),failures=failures,last_input=last_live)
        result['vga_path_passed']=bool(result.get('digital_video_path_verified') and result['physical_display_verified'] and not failures)
        if demo:
            result['demo_completed']=bool(result.get('digital_video_path_verified') and
                result['configuration_recovery_verified'] and result['source_stop_verified'] and not failures)
        replace_json(out/'result.json',result)
        print(json.dumps({key:result[key] for key in ('state','vga_path_passed','configuration_recovery_verified','source_stop_verified','failures')}),flush=True)
    if not result['configuration_recovery_verified'] or not result['source_stop_verified']:
        raise RuntimeError('Experiment recovery not completely verified; retain all evidence')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--demo',action='store_true',help='持续演示；Ctrl+C 有序退出，不自动结束或要求重复验收')
    args=parser.parse_args()
    if args.check:
        print(json.dumps(check()))
    else:
        with SessionLock(ROOT/'build/locks/demo_launcher.lock'):
            credential=getpass.getpass('PYNQ sudo password (not recorded): ')
            stop=threading.Event()
            previous=signal.signal(signal.SIGINT,lambda *_:stop.set())
            try:
                run(credential,demo=args.demo,stop_requested=stop.is_set)
            finally:
                credential=None
                signal.signal(signal.SIGINT,previous)
