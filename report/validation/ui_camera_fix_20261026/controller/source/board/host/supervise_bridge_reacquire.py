"""Owned, bounded N4 event experiment; never a nominal performance acceptance."""
import argparse,datetime,hashlib,json,subprocess,sys,time
from pathlib import Path
from longrun_process import OwnedProcess,powershell,qpc_ns
from bridge_matrix_policy import Monitor
from longrun_lease import refresh as refresh_lease
from longrun_heartbeat import latest as heartbeat_latest
from ddr_device_status import properties

ROOT=Path(__file__).resolve().parents[2]
SCRIPTS=ROOT/'board/host'
BIN=Path('D:/vivado2026.1/2026.1/Vivado/bin')
CANDIDATE=ROOT/'build/frozen/nexys_2026-10-08_bridge_reacquire_sram_r1'
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def write(path,value):
    tmp=path.with_suffix(path.suffix+'.pending')
    tmp.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8');tmp.replace(path)
def touch(path,reason):
    if not path.exists():path.write_text(reason+'\n',encoding='ascii')
def operation(folder):
    try:return dict(line.split('=',1) for line in (folder/'operation.txt').read_text().splitlines() if '=' in line)
    except OSError:return {}
def recovered(folder):
    if operation(folder).get('recovery_exit')!='0':return False
    try:properties(folder/'device_restored.rpt','00000000')
    except (OSError,ValueError,AssertionError,KeyError):return False
    return True
def wait_process(process,seconds):
    until=qpc_ns()+int(seconds*1e9)
    while process.poll() is None and qpc_ns()<until:time.sleep(.2)
    return process.poll()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('package',type=Path);p.add_argument('destination',type=Path);p.add_argument('--case',choices=('nominal','startup','hdmi','dp'),required=True);a=p.parse_args()
    from bridge_reacquire_candidate import check_package as check
    package=a.package.resolve();check(package)
    out=a.destination.resolve();out.relative_to(ROOT/'board/acceptance');out.mkdir(parents=True,exist_ok=False)
    control=out/'control';control.mkdir();board=out/'board';capture=out/'capture'
    # Deliberately unknown until actual restored device properties are verified.
    result=dict(state='running_recovery_unknown',configuration_recovery_verified=False,physical_recovery_verified=False,
        n4_passed=False,formal_release=False,candidate='26100801',experiment_case=a.case,started_at=utc(),
        package_sha256=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest(),
        candidate_sha256=hashlib.sha256((CANDIDATE/'candidate.json').read_bytes()).hexdigest())
    write(out/'result.json',result)
    write(out/'plan.json',dict(case=a.case,warmup_seconds=30,hold_limit_seconds=600,window_limit_seconds=480,stable_seconds=20,operator_events=['baseline_observed','action_prompt','action_confirmed','recovery_observed'],nominal_performance_test=False,n4_passed=False))
    processes=[];server=None;serial=None;loader=None;monitor=None
    events=(out/'events.jsonl').open('x',encoding='utf-8')
    def event(kind,**details):
        events.write(json.dumps(dict(event=kind,utc=utc(),qpc_ns=qpc_ns(),**details))+'\n');events.flush()
    def spawn(name,executable,args):
        proc=OwnedProcess(out/name,executable,args,ROOT);processes.append(proc)
        event('launch',name=name,pid=proc.process.pid);return proc
    def vivado(name,script,args):
        return spawn(name,BIN/'vivado.bat',['-mode','batch','-source',SCRIPTS/script,
            '-log',out/(name+'.log'),'-journal',out/(name+'.jou'),'-tclargs',*args])
    def close_owned(proc):
        try:proc.close();return True
        except BaseException as e:
            result.setdefault('process_cleanup_errors',[]).append(str(e));return False
    try:
        # Inventory is read-only and a collision is a hard stop, never kill by name.
        inventory=spawn('inventory',powershell(),['-NoProfile','-NonInteractive','-File',SCRIPTS/'longrun_inventory.ps1','-Destination',out/'inventory.json'])
        if wait_process(inventory,30)!=0:raise RuntimeError('Inventory failed or timed out')
        inventory.close()
        server=spawn('server',BIN/'unwrapped/win64.o/hw_server.exe',['-s','tcp::3121'])
        serial=spawn('serial',powershell(),['-NoProfile','-NonInteractive','-File',SCRIPTS/'capture_bridge_matrix_uart.ps1','-Destination',capture])
        deadline=qpc_ns()+int(30e9)
        while not (capture/'ready.txt').exists() and serial.poll() is None and qpc_ns()<deadline:time.sleep(.2)
        if not (capture/'ready.txt').exists() or serial.poll() is not None:raise RuntimeError('UART not ready; no load')
        (control/'lease.txt').write_text('0')
        loader=vivado('loader','run_bridge_reacquire.tcl',[sys.executable,CANDIDATE,board,control,package])
        monitor=Monitor(qpc_ns()/1e9);last_data=None;last_size=0;last_beat=qpc_ns()/1e9
        identity=json.loads((capture/'identity.json').read_text('utf-8-sig'));frequency=identity['qpc_frequency']
        while True:
            now=qpc_ns()/1e9
            retries=refresh_lease(control,qpc_ns())
            if retries:event('lease_retry',retries=retries)
            now=qpc_ns()/1e9
            try:
                stamp=heartbeat_latest(capture/'heartbeat.log')
                if stamp is None:raise ValueError('No complete heartbeat yet')
                beat=stamp/frequency
                if beat>now+.1:raise RuntimeError('Capture clock is incompatible')
                last_beat=max(last_beat,beat)
            except (OSError,ValueError):pass # Short overwrite/read overlap; freshness still expires.
            size=(capture/'capture.bin').stat().st_size
            if size>last_size:last_data=now;last_size=size
            if (board/'active.txt').exists() and last_data is None:last_data=now
            capture_ok=serial.poll() is None and now-last_beat<10 and (monitor.active is None or last_data is not None and now-last_data<10)
            actions=monitor.tick(now,active=(board/'active.txt').exists(),programming=(board/'programming_attempted.txt').exists(),
                sample_ack=(board/'sample_started.txt').exists(),loader_done=loader.poll() is not None,
                capture_ok=capture_ok and server.poll() is None,cancel=(out/'cancel.request').exists(),finish_requested=((out/'finish.request').exists() or a.case=='nominal' and monitor.sample is not None and now-monitor.sample>=120))
            for action in actions:
                event(action,reason=monitor.reason)
                if action=='stop':touch(control/'stop.request',monitor.reason)
                elif action=='sample_start':touch(control/'sample_start.request','Start event-experiment window after warmup')
                elif action=='finish':touch(control/'finish.request','Operator requested bounded experiment completion')
            if 'terminate' in actions:
                loader.close();result['forced_loader_termination']=True;break
            if 'done' in actions:break
            time.sleep(.5)
        result['monitor']=monitor.summary()
        result['loader_exit']=loader.poll()
        result['execution_complete']=bool(monitor.summary()['experiment_complete'] and loader.poll()==0 and operation(board).get('sample_finished')=='1')
    except BaseException as e:
        result['error']=str(e);event('exception',message=str(e))
        if loader and loader.poll() is None:
            touch(control/'stop.request','Supervisor exception; recover')
            if wait_process(loader,60) is None:result['forced_loader_termination']=True
    finally:
        loader_stopped=close_owned(loader) if loader else True
        result['configuration_recovery_verified']=recovered(board)
        if not loader_stopped:result['configuration_recovery_verified']=False
        if loader_stopped and (board/'programming_attempted.txt').exists() and not result['configuration_recovery_verified']:
            # Recreate only our owned server, after our loader tree is stopped.
            if server:server.close()
            try:
                server=spawn('recovery_server',BIN/'unwrapped/win64.o/hw_server.exe',['-s','tcp::3121'])
                for attempt in (1,2):
                    dest=out/f'recovery_{attempt}'
                    recover=vivado(f'recover_job_{attempt}','recover_hd1080_long.tcl',[board/'programming_attempted.txt',dest])
                    code=wait_process(recover,90);recover.close()
                    event('recovery_attempt',attempt=attempt,exit=code,verified=recovered(dest))
                    if code==0 and recovered(dest):
                        result['configuration_recovery_verified']=True;result['recovery_directory']=str(dest);break
            except BaseException as e:result['recovery_exception']=str(e)
        if capture.exists():touch(capture/'stop.request','Test ended; close read-only UART')
        if serial:wait_process(serial,10)
        for proc in reversed(processes):close_owned(proc)
        if result.get('process_cleanup_errors'):result['execution_complete']=False
        result['state']='ended';result['ended_at']=utc()
        result['physical_recovery_verified']=False
        result['recovery_status']='configuration_verified_visual_pending' if result['configuration_recovery_verified'] else 'unknown'
        result['onsite_action_required']=bool((board/'programming_attempted.txt').exists() and not result['configuration_recovery_verified'])
        write(out/'result.json',result);event('end',recovery=result['recovery_status']);events.close()
    print(json.dumps(result))
    return 0 if result.get('execution_complete') and result['configuration_recovery_verified'] else 1

if __name__=='__main__':raise SystemExit(main())
