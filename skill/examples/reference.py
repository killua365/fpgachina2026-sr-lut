"""Scalar golden references; do not call these from the mock backend."""
import numpy as np


def golden(source, example, parameter):
    values = []
    for item in source.flat:
        value = int(item)
        if example == "image_threshold":
            values.append(255 if value >= parameter else 0)
        elif example == "sensor_gain":
            values.append(max(-32768, min(32767, value * parameter)))
        else:
            raise ValueError("Unknown example")
    return np.array(values, dtype=source.dtype).reshape(source.shape)


def make_input(example, shape):
    size = int(np.prod(shape))
    if example == "image_threshold":
        # Includes 0,255 and each threshold neighbor for standard threshold=128.
        return (np.arange(size, dtype=np.int64) % 256).astype("u1").reshape(shape)
    if example == "sensor_gain":
        edges = [-32768, -20000, -10923, -1, 0, 1, 10922, 20000, 32767]
        return np.resize(np.array(edges, dtype="<i2"), size).reshape(shape)
    raise ValueError("Unknown example")
