"""Read the last complete record from the append-only heartbeat ledger."""

def latest(path):
    with path.open('rb') as stream:
        stream.seek(0,2);end=stream.tell();stream.seek(max(0,end-512))
        data=stream.read(512)
    complete,separator,_=data.rpartition(b'\n')
    if not separator:return None
    line=complete.rsplit(b'\n',1)[-1].strip()
    if not 1<=len(line)<=20 or not line.isdigit():return None
    return int(line)
