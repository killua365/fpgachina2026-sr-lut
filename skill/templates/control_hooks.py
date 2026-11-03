"""Example hook only for an IP explicitly documented as HLS ap_ctrl_hs.

For custom RTL, implement the same methods from its own register contract.
Never apply HLS start/done masks to an arbitrary peripheral.
"""
from templates.pynq_backend import resolve_ip


class HlsControlHooks:
    def __init__(self, ip_name, scalar_writes=()):
        self.ip_name = ip_name
        self.scalar_writes = tuple(scalar_writes)
        for offset, value in self.scalar_writes:
            if type(offset) is not int or offset < 0x10 or offset % 4:
                raise ValueError("Scalar offsets must be aligned and outside control block")
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError("Scalar value must be uint32")
        self.done_seen = False

    def configure(self, overlay, contract):
        ip = resolve_ip(overlay, self.ip_name)
        if not ip.read(0) & 4:
            raise RuntimeError("HLS core is not idle before configure")
        self.done_seen = False
        for offset, value in self.scalar_writes:
            ip.write(offset, value)

    def start(self, overlay):
        resolve_ip(overlay, self.ip_name).write(0, 1)

    def done(self, overlay):
        self.done_seen |= bool(resolve_ip(overlay, self.ip_name).read(0) & 2)
        return self.done_seen
