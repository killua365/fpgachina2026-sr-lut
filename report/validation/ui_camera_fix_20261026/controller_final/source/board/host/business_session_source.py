"""Bounded SSH adapter with a qualified business package and live PID binding."""
import base64
import json
import queue
import threading
from collections import deque
from pathlib import Path
import shlex
import subprocess
import time
import uuid
from business_session_remote import validate_launch, verify_final, require

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT/'board/release/source.json'
QUERY_TIMEOUT = 12


class ProbeConnection:
    """One unprivileged read-only process; each reply still has a new challenge."""
    def __init__(self, command):
        self.lines = queue.Queue(maxsize=2)
        self.errors = deque(maxlen=16)
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding='utf-8', bufsize=1,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.readers = [threading.Thread(target=self._stdout,daemon=True),
                        threading.Thread(target=self._stderr,daemon=True)]
        for reader in self.readers: reader.start()

    def _stdout(self):
        try:
            while True:
                line=self.process.stdout.readline(4*1024*1024+1)
                if not line: break
                if len(line)>4*1024*1024 or not line.endswith('\n'):
                    raise ValueError('Oversized or incomplete probe response')
                self.lines.put_nowait(line)
        except BaseException as exc:
            try: self.lines.put_nowait(exc)
            except queue.Full: pass
        finally:
            try: self.lines.put_nowait(EOFError('Probe connection ended'))
            except queue.Full: pass

    def _stderr(self):
        for line in self.process.stderr:
            self.errors.append(line[-1200:])

    def query(self, request):
        self.process.stdin.write(json.dumps(request)+'\n')
        self.process.stdin.flush()
        try: line=self.lines.get(timeout=QUERY_TIMEOUT)
        except queue.Empty:
            raise subprocess.TimeoutExpired('persistent_read_only_probe',QUERY_TIMEOUT,
                                             stderr=''.join(self.errors)) from None
        if isinstance(line,BaseException): raise line
        envelope=json.loads(line)
        if 'error' in envelope: raise RuntimeError('Source inspect failed: '+envelope['error'])
        return subprocess.CompletedProcess('persistent_read_only_probe',0,
            json.dumps(envelope['value']), ''.join(self.errors)+envelope.get('diagnostics',''))

    def close(self):
        # Terminating this read-only SSH peer cannot stop or change the worker.
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=3)
        for reader in self.readers: reader.join(timeout=1)
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr): stream.close()


def probe_server_code():
    source=Path(__file__).with_name('business_session_remote.py').read_text('utf-8')
    return ("import json,sys,time\nns={'__name__':'probe_server'}\nexec("+repr(source)+",ns)\n"
            "for line in sys.stdin:\n"
            " stages=[]\n"
            " ns['stage']=lambda name: stages.append({'stage':name,'monotonic':time.monotonic()})\n"
            " try:\n"
            "  if len(line)>131072: raise ValueError('Probe request too large')\n"
            "  request=json.loads(line)\n"
            "  if request.get('action')!='inspect': raise ValueError('Read-only probe')\n"
            "  value=ns['probe'](request)\n"
            "  reply={'value':value,'diagnostics':''.join('SRLUT_PROBE '+json.dumps(s)+'\\n' for s in stages)}\n"
            " except Exception as exc: reply={'error':type(exc).__name__+': '+str(exc)}\n"
            " print(json.dumps(reply),flush=True)\n")


def query_diagnostics(stderr):
    """Keep execution milestones, never SSH command payloads or key paths."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode('utf-8', errors='replace')
    stages, remote, errors = [], [], []
    for line in (stderr or '').splitlines():
        if line.startswith('SRLUT_PROBE '):
            try:
                value = json.loads(line[len('SRLUT_PROBE '):])
                if set(value) == {'stage', 'monotonic'}:
                    remote.append(value)
            except (ValueError, TypeError):
                pass  # Diagnostics never replace response identity validation.
        elif line.startswith('debug') or line.startswith('Authenticated to '):
            for text, name in (('Connection established.', 'connected'),
                               ('Authenticated to ', 'authenticated'),
                               ('Sending command:', 'command_sent')):
                if text in line and name not in stages:
                    stages.append(name)
        else:
            errors.append(line)
    return dict(ssh_stages=stages, remote_stages=remote[-16:],
                stderr_error_tail='\n'.join(errors)[-1200:])


class Source:
    def __init__(self, launch, *, persistent=False):
        self.contract = json.loads(CONTRACT.read_text('utf-8'))
        self.launch = validate_launch(launch, self.contract)
        self.binding = None
        self.last_query = None
        self.persistent, self.connection = persistent, None

    def close(self):
        if self.connection is not None:
            connection,self.connection=self.connection,None
            connection.close()

    def query(self, action='inspect'):
        require(action in ('inspect', 'stop'), 'Invalid source action')
        challenge = uuid.uuid4().hex
        request = dict(launch=self.launch, contract=self.contract, action=action, binding=self.binding, challenge=challenge)
        payload = base64.b64encode(json.dumps(request).encode()).decode('ascii')
        command = (['sudo', '-n'] if action == 'stop' else [])+['python3', '-B', '-', payload]
        options = ['-v', '-i', str(Path.home()/'.ssh/sr_lut_pynq_z2_ed25519'), '-o', 'BatchMode=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', f'ConnectTimeout={QUERY_TIMEOUT}', '-o', 'ServerAliveInterval=3',
                   '-o', 'ServerAliveCountMax=1']
        started = time.monotonic()
        # OpenSSH applies ConnectTimeout to the banner/key-exchange stage too.
        # Let it use the existing total budget; subprocess.run still bounds the
        # entire connection, authentication and probe to 12 seconds, without retry.
        self.last_query = dict(action=action, challenge=challenge, timeout_seconds=QUERY_TIMEOUT,
                               connection_timeout_seconds=QUERY_TIMEOUT,
                               outcome='pending')
        try:
            if self.persistent and action=='inspect':
                if self.connection is None:
                    self.connection=ProbeConnection(['ssh.exe',*options,'xilinx@192.168.2.99',
                        shlex.join(['python3','-u','-B','-c',probe_server_code()])])
                result=self.connection.query(request)
            else:
                result = subprocess.run(['ssh.exe', *options, 'xilinx@192.168.2.99', shlex.join(command)],
                    input=Path(__file__).with_name('business_session_remote.py').read_text('utf-8'),
                    capture_output=True, text=True, encoding='utf-8', timeout=QUERY_TIMEOUT)
            self.last_query.update(query_diagnostics(result.stderr), returncode=result.returncode)
            if result.returncode:
                raise RuntimeError('Source '+action+' failed: '+self.last_query['stderr_error_tail'])
            value = json.loads(result.stdout)
            self.accept(value, challenge)
            self.last_query['outcome'] = 'accepted'
            value['query_diagnostics'] = self.last_query
            return value
        except subprocess.TimeoutExpired as exc:
            self.close()
            self.last_query.update(query_diagnostics(exc.stderr), outcome='timeout')
            # Do not retry, extend the deadline, or return the previous response.
            raise RuntimeError('Source '+action+' timed out after 12 seconds') from exc
        except BaseException as exc:
            self.close()
            self.last_query.update(outcome='rejected', error_type=type(exc).__name__)
            raise
        finally:
            self.last_query['elapsed_seconds'] = time.monotonic()-started

    def accept(self, value, challenge):
        require(value.get('challenge') == challenge and value.get('run_id') == self.launch['run_id'] and
                value.get('remote_results') == self.launch['remote_results'], 'Stale or foreign response')
        if value['state'] in ('absent', 'starting'):
            require(self.binding is None, 'Bound worker disappeared')
            return
        binding = value['binding']
        require(type(binding.get('worker_pid')) is int and binding['worker_pid'] > 1 and
                binding.get('boot_id') == value['boot_id'] and
                binding.get('started_monotonic') == value['worker']['started_monotonic'], 'Bad worker binding')
        require(self.binding in (None, binding), 'Worker changed')
        if value['state'] == 'running':
            from business_session_remote import verify_worker
            verify_worker(value['worker'], self.launch, value['boot_id'])
            self.binding = binding
        elif value['state'] == 'finished':
            require(self.binding is not None, 'Final report without live binding')
            verify_final(value, self.launch, self.binding)
        elif value['state'] == 'finalizing':
            from business_session_remote import verify_finalizing
            verify_finalizing(value, self.launch, self.binding)
        else:
            raise ValueError('Unknown source state')
