"""Native RGB channel copies for the HDMI path; no DMA buffer ownership."""
import cv2
import numpy as np
import ctypes
from pathlib import Path
import platform


_native = None
_library_path = Path(__file__).parent / 'native/libhdmi_pixels.so'
if platform.machine().lower().startswith('arm') and _library_path.is_file():
    _native = ctypes.CDLL(str(_library_path))
    _native.hdmi_rgbx_to_rgb.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    _native.hdmi_rgbx_to_rgb.restype = ctypes.c_int
    _native.hdmi_blit_rgb.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                     ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t]
    _native.hdmi_blit_rgb.restype = None
    _yuyv = getattr(_native, 'hdmi_yuyv_bgr', None)
    if _yuyv is not None:
        _yuyv.argtypes = _native.hdmi_blit_rgb.argtypes
        _yuyv.restype = ctypes.c_int
    _paint = getattr(_native, 'hdmi_paint_nearest', None)
    if _paint is not None:
        _paint.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t,
                          ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t]
        _paint.restype = ctypes.c_int
    _paint_rgbx = getattr(_native, 'hdmi_paint_rgbx', None)
    if _paint_rgbx is not None:
        _paint_rgbx.argtypes = _paint.argtypes
        _paint_rgbx.restype = ctypes.c_int
    _checked_copy = getattr(_native, 'hdmi_copy_rgbx_checked', None)
    if _checked_copy is not None:
        _checked_copy.argtypes = _paint.argtypes
        _checked_copy.restype = ctypes.c_int
    _validate = getattr(_native, 'hdmi_validate_rgbx', None)
    if _validate is not None:
        _validate.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        _validate.restype = ctypes.c_int
    _area = getattr(_native, 'hdmi_camera_area', None)
    if _area is not None:
        _area.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        _area.restype = None
    _overlay = getattr(_native, 'hdmi_overlay_spans', None)
    if _overlay is not None:
        _overlay.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        _overlay.restype = None
    _wipe = getattr(_native, 'hdmi_wipe4_checked', None)
    if _wipe is not None:
        _wipe.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                         ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t]
        _wipe.restype = ctypes.c_int


def fill_rgbx(target, color=0):
    """Fill whole RGBX words, including strided rectangles, without channel broadcasting.

    Per-channel NumPy broadcasting is especially expensive on ARM scanout CMA.
    The explicit little-endian word retains identical R,G,B,X bytes on every host.
    """
    array = np.asarray(target)
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 4 or not array.flags.writeable:
        raise ValueError('Fill requires writable RGBX storage')
    if not array.size:
        return
    if array.strides[1:] != (4,1):
        raise ValueError('Fill requires complete, adjacent RGBX pixels')
    word = 0 if color == 0 else int.from_bytes(bytes(color),'little')
    if array.flags.c_contiguous:
        array.view('<u4')[...] = word
    else:
        # NumPy 1.21 requires a contiguous array when changing dtype size.
        # Reinterpret one contiguous row, then retain the original valid row
        # stride/extents; the resulting view touches exactly the same bytes.
        row = array[0].view('<u4').reshape(-1)
        words = np.lib.stride_tricks.as_strided(row,shape=array.shape[:2],
            strides=array.strides[:2],writeable=True)
        words[...] = word


def camera_wipe_checked(sr, source, destination, cut):
    """Exact x4 baseline/SR wipe with complete, including hidden, X validation."""
    if (any(a.dtype != np.uint8 or a.ndim != 3 for a in (sr, source, destination))
            or source.shape[2] != 3 or sr.shape[2] != 4 or destination.shape != sr.shape
            or sr.shape[:2] != (source.shape[0]*4, source.shape[1]*4)
            or source.strides[1:] != (3,1) or sr.strides[1:] != (4,1)
            or destination.strides[1:] != (4,1) or not destination.flags.writeable
            or any(a.strides[0] < a.shape[1]*a.shape[2] for a in (sr, source, destination))
            or min(source.shape[:2]) < 1 or max(source.shape[:2]) > 1024
            or type(cut) is not int or not 0 <= cut <= sr.shape[1]
            or np.shares_memory(destination, sr) or np.shares_memory(destination, source)):
        raise ValueError('Wipe requires independent x4 RGB/RGBX storage and an in-bounds cut')
    native = getattr(_native, 'hdmi_wipe4_checked', None) if _native is not None else None
    if native is None:
        valid = validate_rgbx(np.ascontiguousarray(sr))
        destination[:] = sr
        if cut:
            destination[:, :cut, :3] = source[np.arange(sr.shape[0])[:,None]//4,
                                               np.arange(cut)[None,:]//4]
        destination[...,3] = 0
        return valid
    result = native(sr.ctypes.data, sr.strides[0], source.ctypes.data, source.strides[0],
                    destination.ctypes.data, destination.strides[0], source.shape[1], source.shape[0], cut)
    if result < 0:
        raise ValueError('Native wipe rejected geometry')
    return result == 0


def yuyv_bgr(source, destination):
    if (source.dtype != np.uint8 or source.ndim != 3 or source.shape[2] != 2
            or source.shape[1] % 2 or min(source.shape[:2]) < 1 or max(source.shape[:2]) > 4096
            or destination.dtype != np.uint8 or destination.shape != (*source.shape[:2],3)
            or source.strides[1:] != (2,1) or source.strides[0] < source.shape[1]*2
            or destination.strides[1:] != (3,1) or destination.strides[0] < destination.shape[1]*3
            or not destination.flags.writeable or np.shares_memory(source,destination)):
        raise ValueError('YUYV conversion requires independent full-size uint8 arrays')
    if _native is None or getattr(_native,'hdmi_yuyv_bgr',None) is None:
        cv2.cvtColor(source,cv2.COLOR_YUV2BGR_YUY2,destination)
    elif _native.hdmi_yuyv_bgr(source.ctypes.data,source.strides[0],destination.ctypes.data,
                              destination.strides[0],source.shape[1],source.shape[0]):
        raise ValueError('Native YUYV conversion rejected geometry')
    return destination


def camera_area_rgb(bgr, destination):
    if (_native is None or getattr(_native, 'hdmi_camera_area', None) is None
            or bgr.shape != (480, 640, 3) or destination.shape != (270, 360, 3)):
        return False
    if (bgr.dtype != np.uint8 or destination.dtype != np.uint8
            or bgr.strides[1:] != (3, 1) or bgr.strides[0] < 1920
            or destination.strides[1:] != (3, 1) or destination.strides[0] < 1080
            or not destination.flags.writeable or np.shares_memory(bgr, destination)):
        raise ValueError('Camera area requires independent BGR/RGB arrays')
    _native.hdmi_camera_area(bgr.ctypes.data, bgr.strides[0],
                             destination.ctypes.data, destination.strides[0])
    return True


def overlay_spans(source, destination, spans):
    if (source.shape != destination.shape or source.dtype != np.uint8
            or destination.dtype != np.uint8 or source.shape[-1] != 4
            or not source.flags.c_contiguous or not destination.flags.c_contiguous
            or not destination.flags.writeable or spans.dtype != np.uint32
            or not spans.flags.c_contiguous or spans.ndim != 2 or spans.shape[1] != 2
            or np.shares_memory(source, destination)):
        raise ValueError('Invalid cached overlay storage')
    if len(spans) and np.any(spans[:, 0].astype(np.uint64)+spans[:, 1] > source.nbytes):
        raise ValueError('Overlay span exceeds the canvas')
    if _native is not None and getattr(_native, 'hdmi_overlay_spans', None) is not None:
        _native.hdmi_overlay_spans(source.ctypes.data, destination.ctypes.data,
                                   spans.ctypes.data, len(spans))
    else:
        src, dst = source.reshape(-1), destination.reshape(-1)
        for start, length in spans:
            dst[int(start):int(start)+int(length)] = src[int(start):int(start)+int(length)]


def copy_rgbx_checked(source, destination):
    """Full X validation fused with exact full/half-size RGBX painting."""
    if (source.dtype != np.uint8 or destination.dtype != np.uint8
            or source.ndim != 3 or destination.ndim != 3
            or source.shape[2] != 4 or destination.shape[2] != 4
            or source.strides[1:] != (4,1) or destination.strides[1:] != (4,1)
            or source.strides[0] < source.shape[1]*4 or destination.strides[0] < destination.shape[1]*4
            or not destination.flags.writeable or np.shares_memory(source,destination)
            or min(*source.shape[:2],*destination.shape[:2]) < 1
            or max(*source.shape[:2]) > 4096):
        raise ValueError('Checked copy requires independent uint8 RGBX arrays')
    half = source.shape[:2] == tuple(2*x for x in destination.shape[:2])
    if source.shape != destination.shape and not half:
        raise ValueError('Checked copy supports only full or half size')
    if _native is None or getattr(_native,'hdmi_copy_rgbx_checked',None) is None:
        valid=validate_rgbx(np.ascontiguousarray(source))
        destination[:]=source[::2,::2] if half else source
        return valid
    result=_native.hdmi_copy_rgbx_checked(source.ctypes.data,source.strides[0],source.shape[1],source.shape[0],
        destination.ctypes.data,destination.strides[0],destination.shape[1],destination.shape[0])
    if result < 0:
        raise ValueError('Native checked copy rejected geometry')
    return result == 0


def validate_rgbx(source):
    """Check all X bytes, including the scalar tail; never copy the image."""
    if source.dtype != np.uint8 or source.ndim != 3 or source.shape[2] != 4 or not source.flags.c_contiguous:
        raise ValueError('Validation requires contiguous uint8 RGBX')
    if _native is not None and getattr(_native, 'hdmi_validate_rgbx', None) is not None:
        return not bool(_native.hdmi_validate_rgbx(source.ctypes.data, source.shape[0]*source.shape[1]))
    return not bool(np.any(source[..., 3]))


def paint_nearest(target, source):
    """Fuse exact power-of-two sampling; retain OpenCV for other fit ratios."""
    if _native is None or getattr(_native, 'hdmi_paint_nearest', None) is None:
        return False
    if (target.ndim != 3 or source.ndim != 3 or target.shape[2] != 4 or source.shape[2] != 3
            or target.dtype != np.uint8 or source.dtype != np.uint8
            or not target.flags.writeable or np.shares_memory(target, source)):
        raise ValueError('Fused paint needs independent writable uint8 RGBX and RGB source')
    if min(*target.shape[:2], *source.shape[:2]) < 1:
        return False
    # ARM OpenCV nearest uses rounded floating reciprocals for arbitrary ratios.
    # Keep its existing pixels in those cases; the standard 720p layouts use
    # exact 1x/2x/4x or reciprocal ratios, where integer sampling agrees.
    if not all(a == b or a in (2*b, 4*b) or b in (2*a, 4*a)
               for a, b in zip(target.shape[:2], source.shape[:2])):
        return False
    if (max(*target.shape[:2], *source.shape[:2]) > 4096 or
            source.strides[-2:] not in ((3, 1), (4, 1)) or target.strides[-2:] != (4, 1) or
            source.strides[0] <= 0 or target.strides[0] <= 0):
        return False
    painter = _native.hdmi_paint_nearest
    if source.strides[-2] == 4:
        # The native vector loads include X. Establish that the final X byte
        # belongs to the backing ndarray, even for cropped RGB-only views.
        backing = source
        while isinstance(backing.base, np.ndarray):
            backing = backing.base
        end = source.ctypes.data + (source.shape[0]-1)*source.strides[0] + source.shape[1]*4
        if (not backing.flags.c_contiguous or source.ctypes.data < backing.ctypes.data
                or end > backing.ctypes.data + backing.nbytes):
            return False
        painter = getattr(_native, 'hdmi_paint_rgbx', None)
        if painter is None:
            return False
    if painter(source.ctypes.data, source.strides[0], source.shape[1], source.shape[0],
                                 target.ctypes.data, target.strides[0], target.shape[1], target.shape[0]):
        raise ValueError('Native paint rejected geometry')
    return True


def fused_unpack_rgb(source, destination):
    """Validate every X byte while copying RGB; return None if unavailable."""
    if _native is None:
        return None
    if (source.dtype != np.uint8 or source.shape[2] != 4 or not source.flags.c_contiguous
            or destination.dtype != np.uint8 or not destination.flags.c_contiguous
            or destination.shape != (*source.shape[:2], 3) or np.shares_memory(source, destination)):
        raise ValueError('Native unpack requires independent contiguous RGBX/RGB arrays')
    return not bool(_native.hdmi_rgbx_to_rgb(source.ctypes.data, destination.ctypes.data,
                                           source.shape[0] * source.shape[1]))


def blit_rgb(target, source):
    """Copy RGB into RGBX, retaining X and supporting a panel's row stride.

    Use the NumPy fallback for singleton axes rejected by the board's OpenCV
    4.5.4. No source or destination allocation is retained by this function.
    """
    if target.shape[:2] != source.shape[:2] or target.shape[2] != 4 or source.shape[2] != 3:
        raise ValueError('RGB/RGBX blit geometry mismatch')
    if min(source.shape[:2]) == 0:
        return
    if (_native is not None and source.dtype == target.dtype == np.uint8 and
            source.strides[-2:] == (3, 1) and target.strides[-2:] == (4, 1) and
            source.strides[0] > 0 and target.strides[0] > 0):
        if np.shares_memory(source, target):
            raise ValueError('Native blit must not alias its source')
        _native.hdmi_blit_rgb(source.ctypes.data, source.strides[0], target.ctypes.data,
                             target.strides[0], source.shape[1], source.shape[0])
        return
    if min(source.shape[:2]) == 1:
        target[..., :3] = source
    else:
        cv2.mixChannels([np.asarray(source)], [np.asarray(target)], [0, 0, 1, 1, 2, 2])


def unpack_rgb(source, destination=None):
    """Return independent contiguous RGB storage, never a borrowed DMA view."""
    if destination is None:
        destination = np.empty((*source.shape[:2], 3), dtype=np.uint8)
    if (destination.shape != (*source.shape[:2], 3) or destination.dtype != np.uint8
            or not destination.flags.c_contiguous or np.shares_memory(source, destination)):
        raise ValueError('RGB output must be independent contiguous uint8 storage')
    if min(source.shape[:2]) == 1:
        destination[...] = source[..., :3]
    else:
        cv2.cvtColor(np.asarray(source), cv2.COLOR_RGBA2RGB, destination)
    return destination
