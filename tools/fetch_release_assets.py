"""Restore versioned Release assets with SHA-256 checks; standard library only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def token():
    value = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if value: return value
    result = subprocess.run(['git', '-c', 'credential.interactive=false', 'credential', 'fill'],
        input='protocol=https\nhost=github.com\n\n', text=True, capture_output=True,
        env={**os.environ, 'GCM_INTERACTIVE': 'never', 'GIT_TERMINAL_PROMPT': '0'}, timeout=30)
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    return fields.get('password') if result.returncode == 0 else None


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None: new.remove_header('Authorization')
        return new


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true', help='Also restore the archived development history')
    parser.add_argument('--check', action='store_true', help='Only verify local files; no network or modifications')
    args = parser.parse_args()
    manifest = json.loads((ROOT/'tools/release_assets.json').read_text('utf-8'))
    selected = [x for x in manifest['files'] if args.history or not x.get('optional_history')]
    missing = []
    for entry in selected:
        path = (ROOT/entry['path']).resolve(); path.relative_to(ROOT)
        if not path.is_file() or path.stat().st_size != entry['bytes'] or sha(path) != entry['sha256']:
            missing.append(entry)
    if args.check:
        print(json.dumps({'passed': not missing, 'checked': len(selected), 'missing_or_changed': [x['path'] for x in missing]}, indent=2))
        return 1 if missing else 0
    if missing:
        secret = token()
        headers = {'User-Agent': 'sr-lut-fpga-assets', 'Accept': 'application/vnd.github+json'}
        if secret: headers['Authorization'] = 'Bearer '+secret
        opener = urllib.request.build_opener(SafeRedirect())
        base = 'https://api.github.com/repos/'+manifest['repository']
        def get_json(url):
            with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
                return json.load(response)
        try:
            release = get_json(base+'/releases/tags/'+manifest['tag'])
            assets = {}
            page = 1
            while True:
                batch = get_json(base+'/releases/'+str(release['id'])+'/assets?per_page=100&page='+str(page))
                assets.update({x['name']:x for x in batch})
                if len(batch)<100: break
                page += 1
            for entry in missing:
                asset = assets[entry['name']]
                path = (ROOT/entry['path']).resolve(); path.relative_to(ROOT)
                path.parent.mkdir(parents=True, exist_ok=True)
                partial = path.with_name(path.name+'.download')
                print('Downloading '+entry['path'], flush=True)
                request = urllib.request.Request(base+'/releases/assets/'+str(asset['id']),
                    headers={**headers, 'Accept':'application/octet-stream'})
                with opener.open(request, timeout=120) as src, partial.open('wb') as dst:
                    for block in iter(lambda:src.read(1024*1024), b''): dst.write(block)
                if partial.stat().st_size!=entry['bytes'] or sha(partial)!=entry['sha256']:
                    raise ValueError('Downloaded asset failed integrity check: '+entry['path'])
                partial.replace(path)
        except urllib.error.HTTPError as error:
            raise SystemExit('GitHub returned HTTP %s. For this private repository, sign in to Git Credential Manager or set GH_TOKEN.' % error.code)
    if args.history:
        entry = next(x for x in selected if x.get('optional_history'))
        subprocess.run(['git','fetch',str(ROOT/entry['path']),
                        'delivery/current:refs/qualification/delivery-current'],cwd=ROOT,check=True)
    print('All selected Release assets verified.', flush=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
