"""Qualified ordered-exit v3 controller. All hardware use requires a fresh baseline."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from bridge_ordered_policy import Monitor, LIMITS
from bridge_ordered_protocol import receive, send
from bridge_ordered_source import Source
from bridge_reacquire_live import inspect
from longrun_process import OwnedProcess, powershell, qpc_ns
from longrun_lease import refresh
from longrun_heartbeat import latest
from supervise_bridge_reacquire import recovered, wait_process, operation

ROOT=Path(__file__).resolve().parents[2]
SCRIPTS=ROOT/'board/host'
BIN=Path('D:/vivado2026.1/2026.1/Vivado/bin')
CANDIDATE=ROOT/'build/frozen/nexys_2026-10-08_bridge_reacquire_sram_r1'


def utc(): return datetime.now(timezone.utc).isoformat()
def read(path): return json.loads(path.read_text('utf-8-sig'))
from bridge_ordered_json import write
from bridge_ordered_transition import read_transition


def baseline_check(value,launch,now=None):
    now=now or datetime.now(timezone.utc)
    stamp=datetime.fromisoformat(value['recorded_host_utc'])
    age=(now-stamp).total_seconds()
    if not (0<=age<=1800 and value.get('authority')=='user' and value.get('run_id')==launch['run_id'] and
            value.get('original_pattern_normal') is True and value.get('osd')=='1280x1024@60' and
            value.get('onsite_ready') is True and value.get('cables_verified') is True):
        raise ValueError('Fresh same-run user baseline/observation availability required')


def run(package,out,launch,baseline,case,source_stop_mode='operator'):
    if source_stop_mode not in ('operator','sudo'): raise ValueError('Unknown source stop mode')
    from bridge_ordered_gate_v3 import check
    check(package)
    source=Source(launch)
    baseline_check(baseline,launch)
    initial=source.query()
    if case=='startup':
        if initial['state']!='absent': raise ValueError('Nexys-first startup needs its source not yet started')
    else:
        required=LIMITS['load']+LIMITS['warmup']+(120 if case=='nominal' else LIMITS['window'])+LIMITS['source_stop']+15
        if initial['state']!='running' or initial['remaining_seconds']<required:
            raise ValueError('Source not running or insufficient bounded source time')
    out=out.resolve(); out.relative_to(ROOT/'board/acceptance'); out.mkdir(parents=True,exist_ok=False)
    control=out/'control'; control.mkdir(); board=out/'board'; capture=out/'capture'
    run_id=launch['run_id']; processes=[]; loader=serial=server=None
    result=dict(schema='n4_ordered_exit_v1',state='running',candidate='26100801',run_id=run_id,
                experiment_case=case,started_at=utc(),configuration_recovery_verified=False,
                source_stop_verified=False,physical_recovery_verified=False,n4_passed=False,formal_release=False)
    plan=dict(schema='n4_ordered_exit_v1',run_id=run_id,case=case,limits=LIMITS,
              source_stop_mode=source_stop_mode,
              source_launch_sha256=hashlib.sha256(json.dumps(launch,sort_keys=True).encode()).hexdigest(),
              tool_manifest_sha256=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest(),
              formal_boundary='host QPC on valid loader acknowledgement; closure precedes source-stop request')
    write(out/'plan.json',plan); write(out/'source_launch.json',launch); write(out/'baseline.json',baseline)
    write(out/'source_initial.json',initial); write(out/'result.json',result)
    events=(out/'events.jsonl').open('x',encoding='utf-8')
    def event(kind,**details):
        row=dict(event=kind,qpc_ns=qpc_ns(),utc=utc(),**details)
        events.write(json.dumps(row,ensure_ascii=False)+'\n'); events.flush()
        if kind in ('start_sample','sample_closed','operator_source_stop_prompt','onsite_source_stop_required','restore','end'):
            print(json.dumps(row,ensure_ascii=False),flush=True)
        return row
    def spawn(name,exe,args):
        proc=OwnedProcess(out/name,exe,args,ROOT); processes.append(proc)
        event('launch',name=name,pid=proc.process.pid); return proc
    def vivado(name,script,args):
        return spawn(name,BIN/'vivado.bat',['-mode','batch','-source',SCRIPTS/script,
                      '-log',out/(name+'.log'),'-journal',out/(name+'.jou'),'-tclargs',*args])
    def close_owned(proc):
        try: proc.close(); return True
        except BaseException as exc:
            result.setdefault('cleanup_errors',[]).append(str(exc)); return False
    monitor=Monitor(qpc_ns()/1e9,case)
    executor=ThreadPoolExecutor(max_workers=1)
    future=None; future_action=None; next_query=0; snapshot=initial; snapshot_time=monitor.started
    stop_pending=False; stop_sent=False; absent_since=None; absent_sequence=None; transition=None
    last_data=None; last_size=0; last_beat=monitor.started; fault=None; counter=0
    try:
        inventory=spawn('inventory',powershell(),['-NoProfile','-NonInteractive','-File',SCRIPTS/'longrun_inventory.ps1','-Destination',out/'inventory.json'])
        if wait_process(inventory,30)!=0: raise RuntimeError('Inventory failed')
        inventory.close()
        server=spawn('server',BIN/'unwrapped/win64.o/hw_server.exe',['-s','tcp::3121'])
        serial=spawn('serial',powershell(),['-NoProfile','-NonInteractive','-File',SCRIPTS/'capture_bridge_matrix_uart.ps1','-Destination',capture])
        until=qpc_ns()+30_000_000_000
        while not (capture/'ready.txt').exists() and serial.poll() is None and qpc_ns()<until: time.sleep(.1)
        if not (capture/'ready.txt').exists() or serial.poll() is not None: raise RuntimeError('UART not ready; no SRAM load')
        frequency=read(capture/'identity.json')['qpc_frequency']
        (control/'lease.txt').write_text('0',encoding='ascii')
        loader=vivado('loader','run_bridge_ordered_v3.tcl',[sys.executable,CANDIDATE,board,control,package,run_id])
        while True:
            now=qpc_ns()/1e9
            try: refresh(control,qpc_ns())
            except BaseException as exc: fault='lease_update_failed'; event('lease_error',message=str(exc))
            serial_alive=serial.poll() is None
            stamp=latest(capture/'heartbeat.log')
            if stamp is not None:
                beat=stamp/frequency
                if beat>now+.1: fault='capture_clock_invalid'
                else: last_beat=max(last_beat,beat)
            raw=(capture/'capture.bin').read_bytes()
            if len(raw)>last_size: last_size=len(raw); last_data=now
            active=(board/'active.txt').exists()
            try:
                boundary=read_transition(board/'recovery_started_qpc.txt',
                    None if monitor.restore is None else round(monitor.restore*1e9),qpc_ns)
                if transition is not None and boundary!=transition:
                    raise ValueError('Configuration transition marker changed')
                if boundary is not None and transition is None:
                    transition=boundary;event('configuration_transition',transition_qpc_ns=boundary)
            except (OSError,ValueError) as exc:
                fault='configuration_transition_invalid';event('transition_error',message=str(exc))
            candidate_active=active and transition is None
            capture_ok=serial_alive and server.poll() is None and now-last_beat<10 and (not candidate_active or last_data is not None and now-last_data<10)
            live=None
            if candidate_active:
                try: live=inspect(raw)
                except ValueError as exc: fault='live_telemetry_invalid'; event('live_error',message=str(exc))
            if live and live.get('available') and not live['source_present'] and live['link_ready'] and not live['error_flags']:
                if absent_since is None: absent_since=now; absent_sequence=live['sequence']
            else: absent_since=absent_sequence=None
            input_absent=bool(absent_since is not None and now-absent_since>=2 and live['sequence']!=absent_sequence and capture_ok)
            if future is not None and future.done():
                counter+=1
                try:
                    snapshot=future.result(); snapshot_time=now
                    name=f'source_{counter:04d}.json'; write(out/name,snapshot)
                    event('source_response',action=future_action,path=name,state=snapshot['state'])
                    if snapshot['state']=='finished' and monitor.closed is None: fault='source_ended_before_sample_close'
                    if future_action=='stop': stop_sent=True
                except BaseException as exc:
                    event('source_error',action=future_action,message=str(exc))
                    # A denied sudo request does not become a successful cancellation.
                    # Continue read-only polling to allow the visible terminal Ctrl+C.
                    if future_action=='stop':
                        monitor.fail('automatic_source_cancel_failed'); stop_sent=True
                        event('onsite_source_stop_required',note='Press Ctrl+C once in this run source terminal; no process kill')
                    elif monitor.closed is None: fault='source_probe_failed'
                future=None; next_query=now+2
            automatic_stop=source_stop_mode=='sudo' and stop_pending and not stop_sent
            if future is None and (now>=next_query or automatic_stop):
                future_action='stop' if automatic_stop and source.binding is not None else 'inspect'
                event('source_request',action=future_action)
                future=executor.submit(source.query,future_action)
            source_verified=bool(snapshot.get('state')=='finished' and now-snapshot_time<=8)
            start_ack=receive(board/'start.ack',run_id,'start')
            close_ack=receive(board/'close.ack',run_id,'close')
            now=qpc_ns()/1e9  # Receipt time, never the earlier start of this loop.
            actions=monitor.tick(now,active=active,start_ack=start_ack,close_ack=close_ack,
                       source_verified=source_verified,input_absent=input_absent,loader_done=loader.poll() is not None,
                       capture_ok=capture_ok,cancel=receive(out/'cancel.request',run_id,'cancel'),
                       close=receive(out/'close.request',run_id,'close'),fault=fault)
            fault=None
            for action in actions:
                event(action)
                if action=='start_sample': send(control/'start.request',run_id,'start')
                elif action=='close_sample': send(control/'close.request',run_id,'close')
                elif action=='sample_closed':
                    write(out/'sample_closed.json',dict(run_id=run_id,qpc_ns=round(now*1e9),monitor=monitor.summary()))
                elif action=='stop_source':
                    stop_pending=True
                    if source_stop_mode=='operator':
                        event('operator_source_stop_prompt',note='Formal sampling closed. Press Ctrl+C once in this run source terminal.')
                elif action=='restore':
                    result['source_stop_verified']=bool(source_verified and input_absent and monitor.closed is not None)
                    write(out/'source_before_restore.json',dict(snapshot=snapshot,live=live,snapshot_qpc_ns=round(snapshot_time*1e9),
                          verified=result['source_stop_verified'],qpc_ns=round(now*1e9)))
                    send(control/'restore.request',run_id,'restore')
            write(out/'live_status.json',dict(monitor=monitor.summary(),source_state=snapshot.get('state'),input=live))
            if 'terminate' in actions: result['forced_loader_termination']=True; break
            if 'done' in actions: break
            time.sleep(.1)
        result['loader_exit']=loader.poll()
    except BaseException as exc:
        monitor.fail('controller_exception'); result['error']=str(exc); event('exception',message=str(exc))
        if source_stop_mode=='operator':
            event('operator_source_stop_prompt',note='Run failed. Press Ctrl+C once in this run source terminal; bounded recovery follows.')
        # A controller failure cannot redefine a passing window. Request closure,
        # try the same owned source cancellation, then bound the loader recovery.
        try:
            if loader and loader.poll() is None and not (control/'close.request').exists():
                send(control/'close.request',run_id,'close')
            emergency_until=qpc_ns()/1e9+LIMITS['source_stop']
            emergency_stop_sent=False; emergency_snapshot=None; absent_start=None; absent_seq=None
            while qpc_ns()/1e9<emergency_until:
                try: refresh(control,qpc_ns())
                except BaseException: pass  # Independent loader lease remains authoritative.
                if future is not None and future.done():
                    try:
                        emergency_snapshot=future.result()
                        write(out/'exception_source_stop.json',emergency_snapshot)
                    except BaseException as source_exc:
                        result['exception_source_stop_error']=str(source_exc)
                    future=None
                if future is None:
                    action='stop' if source_stop_mode=='sudo' and source.binding is not None and not emergency_stop_sent else 'inspect'
                    if action=='stop': emergency_stop_sent=True
                    future=executor.submit(source.query,action)
                try:
                    live=inspect((capture/'capture.bin').read_bytes())
                    if live.get('available') and not live['source_present'] and live['link_ready'] and not live['error_flags']:
                        if absent_start is None: absent_start=qpc_ns()/1e9; absent_seq=live['sequence']
                    else: absent_start=absent_seq=None
                    if (emergency_snapshot and emergency_snapshot['state']=='finished' and absent_start is not None and
                            qpc_ns()/1e9-absent_start>=2 and live['sequence']!=absent_seq):
                        result['exception_source_stop_verified']=True; break
                except (OSError,ValueError): pass
                time.sleep(.25)
        except BaseException as stop_exc: result['exception_source_stop_error']=str(stop_exc)
        # No verified stop/close sequence on this path: explicit recovery unknown.
        if loader and loader.poll() is None:
            try:
                if not (control/'restore.request').exists(): send(control/'restore.request',run_id,'restore')
                wait_process(loader,LIMITS['recovery'])
            except BaseException as restore_exc: result['exception_restore_error']=str(restore_exc)
    finally:
        executor.shutdown(wait=True,cancel_futures=True)
        loader_stopped=close_owned(loader) if loader else True
        result['configuration_recovery_verified']=bool(loader_stopped and recovered(board))
        if loader_stopped and (board/'programming_attempted.txt').exists() and not result['configuration_recovery_verified']:
            if server: close_owned(server)
            try:
                server=spawn('recovery_server',BIN/'unwrapped/win64.o/hw_server.exe',['-s','tcp::3121'])
                for attempt in (1,2):
                    dest=out/f'recovery_{attempt}'
                    job=vivado(f'recovery_job_{attempt}','recover_hd1080_long.tcl',[board/'programming_attempted.txt',dest])
                    code=wait_process(job,90); close_owned(job)
                    event('recovery_attempt',attempt=attempt,exit=code,verified=recovered(dest))
                    if code==0 and recovered(dest):
                        result['configuration_recovery_verified']=True; result['recovery_directory']=str(dest); break
            except BaseException as exc: result['recovery_exception']=str(exc)
        if capture.exists() and not (capture/'stop.request').exists():
            (capture/'stop.request').write_text('Ordered run ended; retain complete raw capture\n')
        if serial: wait_process(serial,10)
        for proc in reversed(processes): close_owned(proc)
        result['configuration_transition_verified']=transition is not None
        if (board/'programming_attempted.txt').exists() and transition is None:
            monitor.fail('configuration_transition_missing')
        result.update(state='ended',ended_at=utc(),monitor=monitor.summary(),source_binding=source.binding)
        result['execution_complete']=bool(monitor.summary()['measurement_complete'] and not monitor.failures and
            result.get('loader_exit')==0 and operation(board).get('sample_closed')=='1' and
            result['source_stop_verified'] and result['configuration_recovery_verified'] and
            result['configuration_transition_verified'] and not result.get('cleanup_errors'))
        result['onsite_action_required']=not result['execution_complete']
        write(out/'result.json',result); event('end'); events.close()
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('package',type=Path); p.add_argument('destination',type=Path)
    p.add_argument('--launch',type=Path,required=True); p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--case',choices=('nominal','startup','hdmi','dp'),required=True)
    p.add_argument('--source-stop-mode',choices=('operator','sudo'),default='operator')
    a=p.parse_args(); result=run(a.package.resolve(),a.destination,read(a.launch),read(a.baseline),a.case,a.source_stop_mode)
    print(json.dumps(result)); return 0 if result['execution_complete'] else 1


if __name__=='__main__': raise SystemExit(main())
