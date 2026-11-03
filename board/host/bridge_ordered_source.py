"""Bounded SSH adapter for the immutable fixed source; no detached remote owner."""
import base64
import json
from pathlib import Path
import shlex
import subprocess
import uuid
from bridge_ordered_remote import validate_launch, verify_final, require


class Source:
    def __init__(self, launch):
        self.launch=validate_launch(launch)
        self.binding=None

    def query(self, action='inspect'):
        challenge=uuid.uuid4().hex
        request=dict(launch=self.launch,action=action,binding=self.binding,challenge=challenge)
        payload=base64.b64encode(json.dumps(request).encode()).decode('ascii')
        command=(['sudo','-n'] if action=='stop' else [])+['python3','-B','-',payload]
        options=['-i',str(Path.home()/'.ssh/sr_lut_pynq_z2_ed25519'),'-o','BatchMode=yes',
                 '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5','-o','ServerAliveInterval=3',
                 '-o','ServerAliveCountMax=1']
        result=subprocess.run(['ssh.exe',*options,'xilinx@192.168.2.99',shlex.join(command)],
                              input=Path(__file__).with_name('bridge_ordered_remote.py').read_text('utf-8'),
                              capture_output=True,text=True,encoding='utf-8',timeout=12)
        if result.returncode: raise RuntimeError('Source '+action+' failed: '+result.stderr[-1200:])
        value=json.loads(result.stdout)
        self.accept(value,challenge)
        return value

    def accept(self,value,challenge):
        require(value.get('challenge')==challenge and value.get('run_id')==self.launch['run_id'] and
                value.get('remote_results')==self.launch['remote_results'], 'Stale or foreign source response')
        if value['state'] in ('absent','starting'):
            require(self.binding is None, 'Bound source disappeared'); return
        binding=value['binding']
        require(self.binding in (None,binding), 'Source worker changed')
        if value['state']=='running':
            self.binding=binding
        elif value['state']=='finished':
            require(self.binding is not None, 'Final report without this run live binding')
            verify_final(value,self.launch,self.binding)
        else: raise ValueError('Unknown source state')
