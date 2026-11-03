"""Exclusive, run-bound control records; a prior run cannot finish this one."""
import argparse
import json
from pathlib import Path
import re
from longrun_process import qpc_ns

OPS = ('start', 'close', 'restore', 'cancel')


def token(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Invalid run ID')
    return value


def encode(run_id, operation):
    token(run_id)
    if operation not in OPS: raise ValueError('Invalid operation')
    return f'ordered_exit_v1 {run_id} {operation}\n'


def send(path, run_id, operation):
    content = encode(run_id, operation)
    # Write a complete sibling first; rename never overwrites on Windows.
    pending = path.with_suffix(path.suffix + '.pending')
    with pending.open('x', encoding='ascii', newline='\n') as stream:
        stream.write(content)
    if path.exists(): raise FileExistsError('Duplicate request: ' + str(path))
    pending.rename(path)


def receive(path, run_id, operation):
    if not path.exists(): return False
    if path.is_symlink() or path.read_text('ascii') != encode(run_id, operation):
        raise ValueError('Foreign or corrupt control record: ' + path.name)
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path); p.add_argument('operation', choices=('close', 'cancel'))
    a=p.parse_args(); plan=json.loads((a.run/'plan.json').read_text('utf-8'))
    result=json.loads((a.run/'result.json').read_text('utf-8'))
    if result['state']=='ended': raise ValueError('Run already ended')
    send(a.run/(a.operation+'.request'), plan['run_id'], a.operation)
    print(json.dumps(dict(operation=a.operation, qpc_ns=qpc_ns())))


if __name__=='__main__': main()
