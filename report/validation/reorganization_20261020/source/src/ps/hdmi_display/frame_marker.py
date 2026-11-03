"""Opt-in SR identity on the composed raster, never on golden SR pixels.

Wire v1: 20 grayscale pixels at x=0..19, y=0 hold the network-byte-order
header; y=1 holds its bytewise complement. CRC32 is the standard reflected
IEEE/zlib CRC of the first 16 bytes. A no-result surface is a valid control
marker (flags=0, mode=0, scale=0), never a newly displayed SR result.
"""
from dataclasses import dataclass
import struct
import zlib

import numpy as np

from .frames import FrameToken

MAGIC = b'SRLT'
VERSION = 1
WIDTH, HEIGHT = 20, 2
MODES = {'idle': 0, 'image': 1, 'video': 2, 'camera': 3}
HEADER = struct.Struct('>4sBBBBII')


@dataclass(frozen=True)
class FrameIdentity:
    mode: str
    scale: int
    generation: int
    sequence: int
    has_result: bool


def encode(token, mode, scale, *, has_result=True):
    if mode not in MODES or (has_result and (mode == 'idle' or scale not in (2, 4))):
        raise ValueError('Invalid marker mode/scale')
    if not has_result and (mode != 'idle' or scale != 0 or token.sequence != 0):
        raise ValueError('Invalid no-result marker')
    for value in (token.generation, token.sequence):
        if type(value) is not int or not 0 <= value <= 0xffffffff:
            raise ValueError('Marker identity exhausted its unsigned 32-bit field')
    header = HEADER.pack(MAGIC, VERSION, MODES[mode], scale, int(has_result),
                         token.generation, token.sequence)
    return header + struct.pack('>I', zlib.crc32(header))


def draw(destination, token, mode, scale, *, has_result=True):
    if (destination.dtype != np.uint8 or destination.ndim != 3 or destination.shape[2] != 4
            or destination.shape[0] < HEIGHT or destination.shape[1] < WIDTH):
        raise ValueError('Marker needs an RGBX canvas at least 20x2')
    values = np.frombuffer(encode(token, mode, scale, has_result=has_result), dtype=np.uint8)
    destination[0, :WIDTH, :3] = values[:, None]
    destination[1, :WIDTH, :3] = (values ^ 255)[:, None]
    destination[:HEIGHT, :WIDTH, 3] = 0


def decode(pixels):
    """Host reference for validating captured composition pixels; no FPS claim."""
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] < 3:
        raise ValueError('Marker capture must be RGB uint8')
    region = pixels[:HEIGHT, :WIDTH, :3]
    if region.shape != (HEIGHT, WIDTH, 3) or np.any(region != region[..., :1]):
        raise ValueError('Missing marker or non-grayscale marker')
    if np.any(region[1, :, 0] != (region[0, :, 0] ^ 255)):
        raise ValueError('Marker row complement differs')
    payload = region[0, :, 0].tobytes()
    magic, version, mode, scale, flags, generation, sequence = HEADER.unpack(payload[:16])
    if magic != MAGIC or version != VERSION or flags not in (0, 1) or mode not in MODES.values():
        raise ValueError('Unknown marker format')
    if zlib.crc32(payload[:16]) != struct.unpack('>I', payload[16:])[0]:
        raise ValueError('Marker CRC mismatch')
    name = next(name for name, value in MODES.items() if value == mode)
    # Apply the same semantic constraints, independently of grayscale/CRC checks.
    encode(FrameToken(generation, sequence), name, scale, has_result=bool(flags))
    return FrameIdentity(name, scale, generation, sequence, bool(flags))
