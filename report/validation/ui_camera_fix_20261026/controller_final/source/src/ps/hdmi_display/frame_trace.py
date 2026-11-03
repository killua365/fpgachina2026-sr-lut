"""Record submitted result identities; timestamps belong only to this PYNQ."""
import json
import time


class FrameTrace:
    def __init__(self, path):
        self.stream = path.open('x', encoding='utf-8', newline='\n')
        self.previous = None
        self.count = 0

    def __call__(self, token, mode, scale):
        identity = (token.generation, token.sequence)
        if identity == self.previous:
            return
        if self.previous is not None and identity <= self.previous:
            raise ValueError('Source identity regressed')
        if mode not in ('image', 'video', 'camera') or scale not in (2, 4):
            raise ValueError('Invalid submitted result')
        self.stream.write(json.dumps(dict(generation=identity[0], sequence=identity[1],
            mode={'image': 1, 'video': 2, 'camera': 3}[mode], scale=scale,
            submitted_monotonic_ns=time.monotonic_ns()))+'\n')
        self.stream.flush()
        self.previous = identity
        self.count += 1

    def close(self):
        self.stream.close()
