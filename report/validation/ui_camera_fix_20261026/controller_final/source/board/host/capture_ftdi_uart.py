"""Read the bound UART through FTDI's installed D2XX driver, without UART writes.

The latency timer is changed for this handle and restored on close. No EEPROM,
USB reset, bit-mode change, JTAG access, or transmitted data is used.
"""
import argparse
import ctypes as C
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time

SERIAL = 'A904CVLW'
U32, U8, HANDLE = C.c_uint32, C.c_ubyte, C.c_void_p


class Ftdi:
    def __init__(self):
        self.handle = HANDLE()
        self.old_latency = None
        self.library = Path(os.environ['SystemRoot']) / 'System32/ftd2xx.dll'
        self.dll = C.WinDLL(str(self.library))
        signatures = {
            'FT_OpenEx': [C.c_char_p, U32, C.POINTER(HANDLE)],
            'FT_Close': [HANDLE],
            'FT_GetDeviceInfo': [HANDLE, C.POINTER(U32), C.POINTER(U32), C.c_void_p, C.c_void_p, C.c_void_p],
            'FT_GetDriverVersion': [HANDLE, C.POINTER(U32)],
            'FT_GetLatencyTimer': [HANDLE, C.POINTER(U8)],
            'FT_SetLatencyTimer': [HANDLE, U8],
            'FT_SetBaudRate': [HANDLE, U32],
            'FT_SetDataCharacteristics': [HANDLE, U8, U8, U8],
            'FT_SetFlowControl': [HANDLE, C.c_uint16, U8, U8],
            'FT_ClrDtr': [HANDLE], 'FT_ClrRts': [HANDLE],
            'FT_SetChars': [HANDLE, U8, U8, U8, U8],
            'FT_SetTimeouts': [HANDLE, U32, U32],
            'FT_GetQueueStatus': [HANDLE, C.POINTER(U32)],
            'FT_GetModemStatus': [HANDLE, C.POINTER(U32)],
            'FT_Read': [HANDLE, C.c_void_p, U32, C.POINTER(U32)],
        }
        for name, args in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, U32

    def call(self, name, *args):
        status = getattr(self.dll, name)(*args)
        if status:
            raise OSError(f'{name} failed with FT_STATUS {status}')

    def open(self):
        self.call('FT_OpenEx', SERIAL.encode('ascii'), 1, C.byref(self.handle))
        kind, ident, version = U32(), U32(), U32()
        serial, description = C.create_string_buffer(16), C.create_string_buffer(64)
        self.call('FT_GetDeviceInfo', self.handle, C.byref(kind), C.byref(ident), serial, description, None)
        if (serial.value, ident.value, kind.value) != (SERIAL.encode('ascii'), 0x04036001, 5):
            raise ValueError('Opened device is not the bound FT232R UART')
        latency = U8()
        self.call('FT_GetLatencyTimer', self.handle, C.byref(latency))
        self.old_latency = latency.value
        self.call('FT_GetDriverVersion', self.handle, C.byref(version))
        self.call('FT_SetBaudRate', self.handle, 115200)
        self.call('FT_SetDataCharacteristics', self.handle, 8, 0, 0)
        self.call('FT_SetFlowControl', self.handle, 0, 0, 0)
        self.call('FT_ClrDtr', self.handle)
        self.call('FT_ClrRts', self.handle)
        self.call('FT_SetChars', self.handle, 0, 0, 0, 0)
        self.call('FT_SetTimeouts', self.handle, 250, 0)
        self.call('FT_SetLatencyTimer', self.handle, 2)
        self.call('FT_GetLatencyTimer', self.handle, C.byref(latency))
        if latency.value != 2:
            raise ValueError('D2XX latency readback mismatch')
        return dict(backend='FTDI D2XX', serial=serial.value.decode(), device_type=kind.value,
                    usb_id=f'{ident.value:08x}', driver_version=f'{version.value:08x}',
                    library=str(self.library), latency_before_ms=self.old_latency,
                    latency_ms=latency.value, usb_transfer_size_changed=False)

    def read(self, buffer):
        queued, status, count = U32(), U32(), U32()
        self.call('FT_GetModemStatus', self.handle, C.byref(status))
        # High byte is the UART line-status register: overrun/parity/framing/break.
        errors = (status.value >> 8) & 0x1e
        self.call('FT_GetQueueStatus', self.handle, C.byref(queued))
        if queued.value:
            self.call('FT_Read', self.handle, buffer, min(queued.value, len(buffer)), C.byref(count))
            if count.value > min(queued.value, len(buffer)):
                raise ValueError('Invalid FT_Read count')
        return count.value, errors, queued.value

    def close(self):
        failures = []
        if self.handle.value:
            try:
                if self.old_latency is not None:
                    self.call('FT_SetLatencyTimer', self.handle, self.old_latency)
                    actual = U8()
                    self.call('FT_GetLatencyTimer', self.handle, C.byref(actual))
                    if actual.value != self.old_latency:
                        raise ValueError('Latency restoration readback mismatch')
            except Exception as error:
                failures.append(str(error))
            try:
                self.call('FT_Close', self.handle)
            except Exception as error:
                failures.append(str(error))
            self.handle = HANDLE()
        if failures:
            raise OSError('; '.join(failures))


def write_json(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def capture(destination, identity, device, qpc, frequency, *, seconds=3000,
            monotonic=time.monotonic, sleep=time.sleep, demo=False):
    """Preserve exact received bytes, including data preceding an error indication."""
    if destination.exists():
        raise FileExistsError('Refuse reused capture directory')
    destination.mkdir()
    result = dict(success=False, failure='', bytes=0, line_status_changes=[], max_queue_bytes=0)
    pending = None
    try:
        backend = device.open()
        if demo:
            sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
            from bounded_logs import BoundedLog
        with (BoundedLog(destination/'capture.bin',binary=True) if demo else (destination/'capture.bin').open('xb', buffering=0)) as raw, \
             (BoundedLog(destination/'chunks.csv') if demo else (destination/'chunks.csv').open('x', encoding='utf-8', newline='', buffering=1)) as chunks, \
             (BoundedLog(destination/'heartbeat.log') if demo else (destination/'heartbeat.log').open('x', encoding='ascii', buffering=1)) as heartbeat:
            chunks.write('utc,qpc_ticks,count,total_bytes\n')
            write_json(destination/'identity.json', dict(identity, **backend,
                       started_at=datetime.now().astimezone().isoformat(),
                       qpc_frequency=frequency, writes_performed=False, baud=115200, format='8N1'))
            (destination/'ready.txt').write_text('Bound UART open; read only\n', encoding='ascii')
            start, last_beat = monotonic(), -1
            buffer = C.create_string_buffer(65536)
            while not (destination/'stop.request').exists():
                elapsed = monotonic() - start
                if seconds is not None and elapsed >= seconds:
                    raise TimeoutError('Capture reached independent total limit')
                count, errors, queued = device.read(buffer)
                stamp = qpc()
                result['last_status_qpc_ticks'] = stamp
                changes = result['line_status_changes']
                if not changes or changes[-1]['flags'] != errors:
                    changes.append(dict(qpc_ticks=stamp, flags=errors))
                    if demo and len(changes)>64: changes.pop(0)
                result['max_queue_bytes'] = max(result['max_queue_bytes'], queued)
                if count:
                    data = buffer.raw[:count]
                    if raw.write(data) != count:
                        raise OSError('Short capture file write')
                    if demo: raw.flush()
                    result['bytes'] += count
                    chunks.write(f'{datetime.now().astimezone().isoformat()},{stamp},{count},{result["bytes"]}\n')
                    if demo: chunks.flush()
                # D2XX can retain an earlier overrun until fresh USB data arrive.
                # Preserve status transitions alongside all raw bytes. The final
                # audit rejects errors overlapping the complete candidate run;
                # live CRC/sequence supervision continues to detect byte loss.
                if int(elapsed) != last_beat:
                    last_beat = int(elapsed)
                    heartbeat.write(str(stamp)+'\n')
                    if demo: heartbeat.flush()
                if not count:
                    sleep(.002)
            result['success'] = True
    except BaseException as error:
        result['failure'] = str(error)
        pending = error
    finally:
        try:
            device.close()
            result['device_closed'] = True
            result['latency_restored'] = True
        except Exception as error:
            result['success'] = False
            result['cleanup_failure'] = str(error)
            if pending is None:
                pending = error
        result['ended_at'] = datetime.now().astimezone().isoformat()
        write_json(destination/'exit.json', result)
    if pending is not None:
        raise pending
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--port', required=True)
    parser.add_argument('--pnp-id', required=True)
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    if args.pnp_id.upper() != r'FTDIBUS\VID_0403+PID_6001+A904CVLWA\0000':
        raise ValueError('Unbound PNP identity')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.QueryPerformanceCounter.argtypes = [C.POINTER(C.c_int64)]
    kernel.QueryPerformanceFrequency.argtypes = [C.POINTER(C.c_int64)]
    kernel.GetCurrentProcess.restype = HANDLE
    kernel.SetPriorityClass.argtypes = [HANDLE, U32]
    if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x8000):
        raise C.WinError(C.get_last_error())
    frequency = C.c_int64()
    if not kernel.QueryPerformanceFrequency(C.byref(frequency)):
        raise C.WinError(C.get_last_error())
    def qpc():
        value = C.c_int64()
        if not kernel.QueryPerformanceCounter(C.byref(value)):
            raise C.WinError(C.get_last_error())
        return value.value
    capture(args.destination, dict(device=args.port, pnp_id=args.pnp_id,
            process_priority='AboveNormal'), Ftdi(), qpc, frequency.value,
            seconds=None if args.demo else 3000,demo=args.demo)


if __name__ == '__main__':
    main()
