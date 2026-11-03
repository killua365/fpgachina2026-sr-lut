"""Hardware-free lifecycle checks for the separate continuous VGA entry."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_candidate as app


class Immediate:
    def __init__(self, value): self.value=value
    def done(self): return True
    def result(self, timeout=None): return self.value


class Pool:
    def __init__(self, **kwargs): pass
    def submit(self, fn): return Immediate(fn())
    def shutdown(self, **kwargs): pass


class DemoTests(unittest.TestCase):
    def test_demo_runs_past_one_day_then_orders_source_stop_before_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);here=root/'candidate';here.mkdir()
            launch_path=root/'launch.json'
            launch_path.write_text(json.dumps(dict(run_id='a'*32)))
            state=dict(now=0,source=False,stopped=False,ready=False,jumped=False,restored=False)
            actions=[]

            class Clock:
                @staticmethod
                def monotonic(): return state['now']
                @staticmethod
                def sleep(seconds):
                    if state['ready'] and not state['jumped']:
                        state['now']=86401;state['jumped']=True
                    else: state['now']+=1

            class Owned:
                def __init__(self, folder, executable, arguments, cwd):
                    self.loader=folder.name=='loader';self.cwd=cwd
                    if self.loader:
                        self.board=Path(arguments[-5]);self.control=Path(arguments[-4])
                        self.board.mkdir();(self.board/'active.txt').touch()
                        actions.append(('loader_mode',arguments[-1]))
                def poll(self):
                    if self.loader and (self.control/'restore.request').exists():
                        assert state['stopped'], 'FPGA restored before source closed'
                        state['restored']=True
                        (self.board/'operation.txt').write_text('loaded=1\nrecovery_exit=0\n')
                        return 0
                    return None
                def close(self): pass

            class Device:
                def open(self): return {}
                def read(self, buffer): return 0,0,0
                def close(self): pass

            class Source:
                def __init__(self,*args,**kwargs): pass
                def query(self):
                    return dict(state='finished' if state['stopped'] else 'running' if state['source'] else 'absent',
                                worker=dict(phase='interactive_business'))
                def close(self): pass

            class Console:
                error_labels=set()
                def __init__(self,process): state['source']=True
                def poll_prompt(self): return False
                def interrupt(self): state['stopped']=True;actions.append(('source_stop',state['now']))
                def close(self): pass

            def output(*values,**kwargs):
                if any('"event": "DEMO_READY"' in str(v) for v in values): state['ready']=True

            def live(_):
                return dict(available=True,link_ready=True,error_flags=0,clock_memory_status=0x3b,
                            rx_good=100,source_present=state['source'] and not state['stopped'])

            def send(path,run_id,operation):
                if operation=='restore':
                    self.assertTrue(state['stopped']);actions.append(('restore',state['now']))
                path.write_text(operation)

            with patch.multiple(app,ROOT=root,HERE=here,check=lambda:{'passed':True},
                                prepare=lambda **kw:launch_path,Source=Source,Ftdi=Device,
                                OwnedProcess=Owned,SourceConsole=Console,ThreadPoolExecutor=Pool,
                                source_command=lambda launch:[],refresh=lambda *a:None,
                                qpc_ns=lambda:1,powershell=lambda:'powershell',time=Clock,
                                live_status=live,send=send,receive=lambda *a:True), \
                 patch.object(app.subprocess,'run'), patch.object(app.subprocess,'Popen'), \
                 patch('builtins.print',output):
                result=app.run('unused-test-placeholder',demo=True,
                    stop_requested=lambda:state['now']>86402)
            self.assertTrue(state['jumped'])
            self.assertTrue(result['demo_completed'])
            self.assertFalse(result['physical_display_verified'])
            self.assertEqual(result['failures'],[])
            self.assertIn(('loader_mode','demo'),actions)
            self.assertLess(next(i for i,x in enumerate(actions) if x[0]=='source_stop'),
                            next(i for i,x in enumerate(actions) if x[0]=='restore'))
            self.assertIsNone(result['limits']['total_seconds'])

    def test_bounded_test_deadlines_are_preserved(self):
        limits=app.session_limits(False)
        self.assertEqual(limits['loader_mode'],'timed')
        self.assertTrue(app.expired(limits['showing_seconds'],301))
        self.assertTrue(app.expired(limits['total_seconds'],1051))
        for elapsed in (1801,2401,2941,86401):
            self.assertFalse(app.expired(app.session_limits(True)['total_seconds'],elapsed))


if __name__=='__main__': unittest.main(verbosity=2)
