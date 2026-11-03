"""Validate the loader's actual configuration-transition QPC boundary."""
import re


def read_transition(path, restore_request_ns, clock):
    try:
        text = path.read_text(encoding='ascii')
    except FileNotFoundError:
        return None
    # long_write publishes a newline-terminated record. Do not interpret a
    # partially written marker as either a valid boundary or corrupted UART.
    if not text.endswith('\n'):
        return None
    if not re.fullmatch(r'[0-9]+\n', text):
        raise ValueError('Invalid configuration transition marker')
    value = int(text)
    now = clock()  # After reading: a concurrent valid publication is not future.
    if (restore_request_ns is None or type(restore_request_ns) is not int or
            not 0 < restore_request_ns <= value <= now or
            value - restore_request_ns > 120_000_000_000):
        raise ValueError('Configuration transition outside requested recovery')
    return value
