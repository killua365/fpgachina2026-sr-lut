"""Finish the qualified ACP S2MM transfer without a redundant cache invalidate.

Only the HDMI overlays whose S2MM port is ACP may opt in. The PYNQ driver
implementation is byte-bound because completing a transfer uses its private
active-buffer field. The caller still checks PL DONE/errors/word counts and all
output X bytes. HP display buffers retain their normal cache flushes.
"""
import hashlib
import inspect
from pathlib import Path
import time

from sr_lut_x4_driver import DriverError

DRIVER_SHA256 = '436cc096653f449c1011027991f8061d0f0dca83b02901f1e97bdf3a8ea04fe7'
_qualified_classes = set()
OVERLAY_SHA256 = {
    ('58ee93d840ad639b7cb511002f4ced6dfafe6402805d73cb2a9375451eac4806',
     '996c69bd8511a9cb3b25ed77356fc806003adeaa8a0e5626d16204f707bab243'),
    ('61f1b211098aef8bf1c343dbfc690da87c1a31313c78023c32aca28e73943080',
     '51f72a22e64dd6494907eee473a3e63a666d3eaed5ad36feac8496916a889a7d'),
}


def qualified_overlay(overlay):
    filename = getattr(overlay, 'bitfile_name', None)
    if not isinstance(filename, str):
        return False
    bit = Path(filename)
    hwh = bit.with_suffix('.hwh')
    return (bit.is_file() and hwh.is_file() and
            tuple(hashlib.sha256(p.read_bytes()).hexdigest() for p in (bit,hwh)) in OVERLAY_SHA256)


def qualified_channel(channel):
    cls = type(channel)
    if cls in _qualified_classes:
        return True
    if cls.__module__ != 'pynq.lib.dma' or cls.__name__ != '_SDMAChannel':
        return False
    if hashlib.sha256(Path(inspect.getfile(cls)).read_bytes()).hexdigest() != DRIVER_SHA256:
        raise DriverError('ACP completion requires the qualified PYNQ DMA driver')
    _qualified_classes.add(cls)
    return True


def wait_acp_receive(channel, timeout, *, clock=time.perf_counter, sleep=time.sleep):
    """Caller must establish the ACP route and call qualified_channel first."""
    if timeout <= 0 or channel._offset != 0x30 or channel._flush_before is not False:
        raise DriverError('Invalid ACP receive channel')
    buffer = channel._active_buffer
    if buffer is None or not channel.running:
        raise DriverError('ACP receive has no running buffer')
    deadline = clock() + timeout
    while True:
        status = int(channel._mmio.read(0x34))
        if status & 0x71:
            raise DriverError('ACP receive halted/errored: 0x{:08x}'.format(status))
        if status & 2:
            break
        if clock() >= deadline:
            raise TimeoutError('ACP receive did not finish before its deadline')
        sleep(.0001)
    transferred = int(channel._mmio.read(0x58))
    if transferred != buffer.nbytes:
        raise DriverError('ACP receive byte count mismatch')
    channel.transferred = transferred
    channel._active_buffer = None
