"""Bounded diagnostic streams for ordinary demos, never for acceptance traces."""
from pathlib import Path


class BoundedLog:
    def __init__(self, path, *, binary=False, limit=16*1024*1024, copies=4):
        self.path, self.binary, self.limit, self.copies = Path(path), binary, limit, copies
        self.stream = self._open()

    def _open(self):
        return self.path.open('ab') if self.binary else self.path.open('a', encoding='utf-8')

    def write(self, data):
        size=len(data) if self.binary else len(data.encode('utf-8'))
        if self.stream.tell()+size > self.limit:
            self.stream.close()
            oldest=self.path.with_name(self.path.name+'.'+str(self.copies-1))
            oldest.unlink(missing_ok=True)
            for n in range(self.copies-2,0,-1):
                prior=self.path.with_name(self.path.name+'.'+str(n))
                if prior.exists():
                    prior.replace(self.path.with_name(self.path.name+'.'+str(n+1)))
            if self.path.exists():
                self.path.replace(self.path.with_name(self.path.name+'.1'))
            self.stream=self._open()
        return self.stream.write(data)

    def flush(self): self.stream.flush()
    def close(self): self.stream.close()
    def __enter__(self): return self
    def __exit__(self,*_): self.close()
