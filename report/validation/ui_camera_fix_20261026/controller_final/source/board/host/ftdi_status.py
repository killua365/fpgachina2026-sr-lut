"""Check timestamped D2XX line status over the actual candidate interval."""


def check_status(identity, closed, start_ns, end_ns):
    if identity.get('backend') != 'FTDI D2XX':
        return  # Older captures retain their existing complete-byte audit.
    if not closed.get('device_closed') or not closed.get('latency_restored'):
        raise ValueError('D2XX device restoration incomplete')
    frequency = identity['qpc_frequency']
    rows = closed.get('line_status_changes', [])
    if type(frequency) is not int or frequency <= 0 or not rows:
        raise ValueError('Missing D2XX status chronology')
    times, flags = [], []
    for row in rows:
        stamp, value = row['qpc_ticks'], row['flags']
        if (type(stamp) is not int or type(value) is not int or stamp < 0 or
                value < 0 or value & ~0x1e):
            raise ValueError('Invalid D2XX status record')
        ns = stamp * 1_000_000_000 // frequency
        if times and ns <= times[-1]:
            raise ValueError('D2XX status time regressed')
        times.append(ns)
        flags.append(value)
    last = closed.get('last_status_qpc_ticks')
    if type(last) is not int:
        raise ValueError('Missing D2XX status tail')
    last_ns = last * 1_000_000_000 // frequency
    if not times[0] <= start_ns < end_ns <= last_ns or last_ns < times[-1]:
        raise ValueError('D2XX status does not cover candidate interval')
    for left, right, value in zip(times, times[1:]+[last_ns], flags):
        if value and max(left, start_ns) < min(right, end_ns):
            raise ValueError(f'UART line error during candidate interval: 0x{value:02x}')
