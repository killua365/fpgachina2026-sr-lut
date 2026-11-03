import contextlib,io,json,sys,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'board/host'))
import supervise_bridge_ordered_v2 as s
import bridge_ordered_gate_v2 as gate
from bridge_ordered_protocol import send


class Immediate:
    def __init__(self,*a,**k): pass
    def submit(self,fn,*args):
        class Result:
            def __init__(self):
                try:self.value=fn(*args); self.error=None
                except BaseException as exc:self.error=exc
            def done(self):return True
            def result(self,**kwargs):
                if self.error:raise self.error
                return self.value
        return Result()
    def shutdown(self,**kwargs):pass


class World:
    def __init__(self,out,scenario):
        self.out=out; self.scenario=scenario; self.now=0.; self.procs=[]; self.stop_at=None; self.stopped=False
        self.binding={'worker_pid':123,'boot_id':'boot','started_monotonic':0}
    def sleep(self,n):self.now+=max(n,1)
    def wait(self,process,seconds):
        deadline=self.now+seconds
        while process.poll() is None and self.now<deadline:self.sleep(1)
        return process.poll()
    def query(self,action='inspect'):
        events=self.out/'events.jsonl'
        if self.scenario=='operator' and self.stop_at is None and events.exists() and 'operator_source_stop_prompt' in events.read_text('utf-8'):
            self.stop_at=self.now
        if action=='stop':
            if self.scenario=='ssh_denied':raise RuntimeError('sudo unavailable')
            self.stop_at=self.now
        if self.scenario=='early_source_exit' and self.now>60:self.stopped=True
        if self.stop_at is not None and self.now-self.stop_at>3 and self.scenario!='worker_stuck':self.stopped=True
        return dict(state='finished' if self.stopped else 'running',remaining_seconds=1200-self.now)
    def live(self,raw):
        return dict(available=True,source_present=not self.stopped,link_ready=True,error_flags=0,sequence=int(self.now*20))
    def refresh(self,control,value):
        if self.scenario=='lease_failed' and self.now>70:raise TimeoutError('lease')
        (control/'lease.txt').write_text(str(value));return 0
    def restore(self,path):
        (path/'operation.txt').write_text('recovery_exit=0\nsample_closed=1\n')
        original=ROOT/'sim/software/fixtures/nexys_restored.rpt'
        (path/'device_restored.rpt').write_bytes(original.read_bytes())
    def spawn(self,folder,exe,args,cwd):
        w=self; folder=Path(folder); folder.mkdir(); name=folder.name
        class Proc:
            def __init__(self):
                self.process=type('Pid',(),{'pid':123})();self.closed=False;self.code=None;w.procs.append(self)
                if name=='serial':
                    self.capture=Path(args[-1]);self.capture.mkdir()
                    (self.capture/'ready.txt').write_text('ready');(self.capture/'capture.bin').write_bytes(b'x')
                    (self.capture/'identity.json').write_text(json.dumps(dict(qpc_frequency=1e9)))
                if name=='loader':
                    self.board=w.out/'board';self.board.mkdir();self.control=w.out/'control'
                    (self.board/'programming_attempted.txt').touch();(self.board/'active.txt').touch()
                if name.startswith('recovery_job'):
                    dest=Path(args[-1]);dest.mkdir()
                    if w.scenario=='recovery_failed':(dest/'operation.txt').write_text('recovery_exit=1\n');self.code=1
                    else:w.restore(dest);self.code=0
            def poll(self):
                if self.closed:return self.code if self.code is not None else 99
                if name=='inventory':return 0
                if name=='serial':
                    (self.capture/'heartbeat.log').write_text(str(int(w.now*1e9))+'\n')
                    if not (w.scenario=='uart_stale' and w.now>60):
                        with (self.capture/'capture.bin').open('ab') as stream:stream.write(b'x')
                    if (self.capture/'stop.request').exists():return 0
                    if w.scenario=='uart_died' and w.now>60:return 1
                if name=='loader':
                    if w.scenario in ('loader_died','recovery_failed') and w.now>70:return 1
                    if w.scenario=='user_cancel' and w.now>70 and not (w.out/'cancel.request').exists():send(w.out/'cancel.request','a'*32,'cancel')
                    if w.scenario=='loader_hung':return None
                    for action in ('start','close'):
                        request=self.control/(action+'.request');ack=self.board/(action+'.ack')
                        if request.exists() and not ack.exists():
                            if w.scenario=='missing_close_ack' and action=='close':continue
                            ack.write_bytes(b'foreign' if w.scenario=='corrupt_ack' and action=='close' else request.read_bytes())
                    if (self.control/'restore.request').exists():w.restore(self.board);self.code=0
                return self.code
            def close(self):self.closed=True
        return Proc()


class SupervisorTests(unittest.TestCase):
    def run_case(self,scenario):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();out=root/'board/acceptance/run';package=root/'package';package.mkdir();(package/'manifest.json').write_text('{}')
            world=World(out,scenario);launch={'run_id':'a'*32}
            with patch.object(gate,'check',return_value={'passed':True}),patch.object(s,'ROOT',root),\
                 patch.object(s,'Source',return_value=world),patch.object(s,'baseline_check'),\
                 patch.object(s,'OwnedProcess',world.spawn),patch.object(s,'qpc_ns',lambda:int(world.now*1e9)),\
                 patch.object(s.time,'sleep',world.sleep),patch.object(s,'refresh',world.refresh),\
                 patch.object(s,'ThreadPoolExecutor',Immediate),patch.object(s,'inspect',world.live):
                with patch.object(s,'wait_process',world.wait),contextlib.redirect_stdout(io.StringIO()):
                    result=s.run(package,out,launch,{},'nominal','operator' if scenario=='operator' else 'sudo')
            self.assertTrue(all(p.closed for p in world.procs)); self.assertLess(world.now,1140)
            events=[json.loads(line) for line in (out/'events.jsonl').read_text('utf-8').splitlines()]
            return result,events

    def test_normal_source_stop_before_restore_and_uart_kept(self):
        r,events=self.run_case('success');self.assertTrue(r['execution_complete'])
        self.assertTrue(r['source_stop_verified']);self.assertFalse(r['physical_recovery_verified']);self.assertFalse(r['n4_passed'])
        index={e['event']:i for i,e in enumerate(events)}
        self.assertLess(index['sample_closed'],index['stop_source']);self.assertLess(index['stop_source'],index['restore'])

    def test_transient_status_sharing_conflict_does_not_abort(self):
        replace=Path.replace; attempts=[]
        def conflict(path,target):
            if Path(target).name=='live_status.json' and not attempts:
                attempts.append('sharing conflict');raise PermissionError('reader holds target')
            return replace(path,target)
        with patch.object(Path,'replace',conflict):
            r,_=self.run_case('operator')
        self.assertEqual(len(attempts),1);self.assertTrue(r['execution_complete'])

    def test_persistent_status_sharing_conflict_enters_failed_recovery(self):
        replace=Path.replace; original_write=s.write
        def conflict(path,target):
            if Path(target).name=='live_status.json':raise PermissionError('reader never releases target')
            return replace(path,target)
        def short_retry(path,value):
            return original_write(path,value,timeout=.05)
        with patch.object(Path,'replace',conflict),patch.object(s,'write',short_retry):
            r,events=self.run_case('operator')
        self.assertFalse(r['execution_complete']);self.assertIn('controller_exception',r['monitor']['failures'])
        self.assertTrue(any(e['event']=='exception' for e in events))
        self.assertTrue(r['configuration_recovery_verified']);self.assertFalse(r['n4_passed'])

    def test_bounded_faults_do_not_pass(self):
        for scenario in ('user_cancel','uart_stale','uart_died','loader_died','loader_hung','lease_failed','missing_close_ack','corrupt_ack','early_source_exit','ssh_denied','worker_stuck'):
            with self.subTest(scenario=scenario):
                r,_=self.run_case(scenario);self.assertFalse(r['execution_complete']);self.assertTrue(r['configuration_recovery_verified'])
                self.assertFalse(r['n4_passed'])

    def test_planned_operator_stop_is_explicit_and_sends_no_remote_stop(self):
        r,events=self.run_case('operator');self.assertTrue(r['execution_complete'])
        self.assertTrue(any(e['event']=='operator_source_stop_prompt' for e in events))
        self.assertFalse(any(e['event']=='source_request' and e['action']=='stop' for e in events))

    def test_recovery_failure_requires_onsite_action(self):
        r,_=self.run_case('recovery_failed');self.assertFalse(r['configuration_recovery_verified']);self.assertTrue(r['onsite_action_required'])

    def test_baseline_expiry_identity_and_user_authority(self):
        now=datetime.now(timezone.utc); launch={'run_id':'a'*32}
        value=dict(recorded_host_utc=now.isoformat(),authority='user',run_id='a'*32,original_pattern_normal=True,
                   osd='1280x1024@60',onsite_ready=True,cables_verified=True)
        s.baseline_check(value,launch,now)
        for key,val in [('authority','assistant'),('run_id','b'*32),('osd','1920x1080@60'),('onsite_ready',False),
                        ('recorded_host_utc',(now-timedelta(hours=1)).isoformat())]:
            wrong=dict(value,**{key:val})
            with self.assertRaises(ValueError):s.baseline_check(wrong,launch,now)


if __name__=='__main__':unittest.main()
