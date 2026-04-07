"""Low-level CoreAudio / CoreFoundation helpers via ctypes.

Shared by audio_tap.py (Process Tap lifecycle) and any future CoreAudio
callers.  All functions here are C-level CoreAudio — no ObjC runtime.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Library handles
# ---------------------------------------------------------------------------

_ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))
_cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))

# ---------------------------------------------------------------------------
# CoreAudio constants
# ---------------------------------------------------------------------------

SYSTEM_OBJECT: int = 1
PROP_DEVICES: int = int.from_bytes(b"dev#", "big")
PROP_DEFAULT_OUTPUT: int = int.from_bytes(b"dOut", "big")
SCOPE_GLOBAL: int = int.from_bytes(b"glob", "big")
ELEMENT_MAIN: int = 0
PROP_DEVICE_UID: int = int.from_bytes(b"uid ", "big")
PROP_DEVICE_NAME: int = int.from_bytes(b"lnam", "big")
PROP_TRANSPORT_TYPE: int = int.from_bytes(b"tran", "big")
PROP_NOMINAL_SAMPLE_RATE: int = int.from_bytes(b"nsrt", "big")
PROP_BUFFER_FRAME_SIZE: int = int.from_bytes(b"fsiz", "big")
PROP_TAP_FORMAT: int = int.from_bytes(b"tfmt", "big")

# kAudioDeviceTransportType values
TRANSPORT_BUILTIN: int = int.from_bytes(b"bltn", "big")
TRANSPORT_HDMI: int = int.from_bytes(b"hdmi", "big")
TRANSPORT_DISPLAYPORT: int = int.from_bytes(b"dprt", "big")

# CoreFoundation constants
CF_UTF8: int = 0x08000100
CF_INT32_TYPE: int = 9
CF_ALLOC = None

# ---------------------------------------------------------------------------
# Structs
# ---------------------------------------------------------------------------


class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


# ---------------------------------------------------------------------------
# CF function signatures
# ---------------------------------------------------------------------------

_cf.CFStringCreateWithCString.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_uint32,
]
_cf.CFStringCreateWithCString.restype = ctypes.c_void_p
_cf.CFStringGetCString.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_long,
    ctypes.c_uint32,
]
_cf.CFStringGetCString.restype = ctypes.c_bool
_cf.CFDictionaryCreateMutable.argtypes = [
    ctypes.c_void_p,
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_cf.CFDictionaryCreateMutable.restype = ctypes.c_void_p
_cf.CFDictionarySetValue.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_cf.CFArrayCreateMutable.argtypes = [
    ctypes.c_void_p,
    ctypes.c_long,
    ctypes.c_void_p,
]
_cf.CFArrayCreateMutable.restype = ctypes.c_void_p
_cf.CFArrayAppendValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
_cf.CFNumberCreate.restype = ctypes.c_void_p
_cf.CFRelease.argtypes = [ctypes.c_void_p]

# ---------------------------------------------------------------------------
# CA function signatures
# ---------------------------------------------------------------------------

_ca.AudioObjectGetPropertyDataSize.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(AudioObjectPropertyAddress),
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
]
_ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
_ca.AudioObjectGetPropertyData.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(AudioObjectPropertyAddress),
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_void_p,
]
_ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
_ca.AudioObjectSetPropertyData.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(AudioObjectPropertyAddress),
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_void_p,
]
_ca.AudioObjectSetPropertyData.restype = ctypes.c_int32
_ca.AudioHardwareCreateAggregateDevice.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
]
_ca.AudioHardwareCreateAggregateDevice.restype = ctypes.c_int32
_ca.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]
_ca.AudioHardwareDestroyAggregateDevice.restype = ctypes.c_int32

# ---------------------------------------------------------------------------
# CF callback struct pointers
# ---------------------------------------------------------------------------

CF_ARRAY_CBS = ctypes.c_void_p.in_dll(_cf, "kCFTypeArrayCallBacks")
CF_DICT_KEY_CBS = ctypes.c_void_p.in_dll(_cf, "kCFTypeDictionaryKeyCallBacks")
CF_DICT_VAL_CBS = ctypes.c_void_p.in_dll(_cf, "kCFTypeDictionaryValueCallBacks")

# ---------------------------------------------------------------------------
# CF helpers
# ---------------------------------------------------------------------------


def cfstr(s: str) -> ctypes.c_void_p:
    """Create a CFString from a Python string."""
    return _cf.CFStringCreateWithCString(CF_ALLOC, s.encode("utf-8"), CF_UTF8)


def cfnum(n: int) -> ctypes.c_void_p:
    """Create a CFNumber (Int32) from a Python int."""
    val = ctypes.c_int32(n)
    return _cf.CFNumberCreate(CF_ALLOC, CF_INT32_TYPE, ctypes.byref(val))


def cfdict() -> ctypes.c_void_p:
    """Create an empty mutable CFDictionary."""
    return _cf.CFDictionaryCreateMutable(
        CF_ALLOC,
        0,
        CF_DICT_KEY_CBS,
        CF_DICT_VAL_CBS,
    )


def cfarray() -> ctypes.c_void_p:
    """Create an empty mutable CFArray."""
    return _cf.CFArrayCreateMutable(CF_ALLOC, 0, CF_ARRAY_CBS)


def cfdict_set(d: ctypes.c_void_p, key: str, value: ctypes.c_void_p) -> None:
    """Set a CFString key in a mutable CFDictionary.

    The key CFString leaks (not released) — this matches how CoreAudio's
    own aggregate creation APIs are typically called via ctypes.  The leak
    is negligible since tap setup is a one-time operation.
    """
    k = cfstr(key)
    _cf.CFDictionarySetValue(d, k, value)


def cfarray_append(a: ctypes.c_void_p, value: ctypes.c_void_p) -> None:
    """Append a value to a mutable CFArray."""
    _cf.CFArrayAppendValue(a, value)


def cfrelease(obj: ctypes.c_void_p) -> None:
    """Release a CoreFoundation object."""
    if obj:
        _cf.CFRelease(obj)


# ---------------------------------------------------------------------------
# CoreAudio property helpers
# ---------------------------------------------------------------------------


def get_string_property(device_id: int, selector: int) -> str | None:
    """Read a CFString property from an AudioObject."""
    prop = AudioObjectPropertyAddress(selector, SCOPE_GLOBAL, ELEMENT_MAIN)
    size = ctypes.c_uint32(0)
    if (
        _ca.AudioObjectGetPropertyDataSize(
            device_id, ctypes.byref(prop), 0, None, ctypes.byref(size)
        )
        != 0
    ):
        return None
    cfstr_val = ctypes.c_void_p()
    if (
        _ca.AudioObjectGetPropertyData(
            device_id,
            ctypes.byref(prop),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(cfstr_val),
        )
        != 0
    ):
        return None
    buf = ctypes.create_string_buffer(256)
    if _cf.CFStringGetCString(cfstr_val, buf, 256, CF_UTF8):
        return buf.value.decode("utf-8")
    return None


def get_uint32_property(device_id: int, selector: int) -> int | None:
    """Read a UInt32 property from an AudioObject."""
    prop = AudioObjectPropertyAddress(selector, SCOPE_GLOBAL, ELEMENT_MAIN)
    size = ctypes.c_uint32(4)
    value = ctypes.c_uint32(0)
    if (
        _ca.AudioObjectGetPropertyData(
            device_id,
            ctypes.byref(prop),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(value),
        )
        != 0
    ):
        return None
    return value.value


# ---------------------------------------------------------------------------
# Device enumeration
# ---------------------------------------------------------------------------


def list_device_ids() -> list[int]:
    """Return all AudioObjectIDs for audio devices on the system."""
    prop = AudioObjectPropertyAddress(PROP_DEVICES, SCOPE_GLOBAL, ELEMENT_MAIN)
    size = ctypes.c_uint32(0)
    if (
        _ca.AudioObjectGetPropertyDataSize(
            SYSTEM_OBJECT, ctypes.byref(prop), 0, None, ctypes.byref(size)
        )
        != 0
    ):
        return []
    n = size.value // 4
    devs = (ctypes.c_uint32 * n)()
    if (
        _ca.AudioObjectGetPropertyData(
            SYSTEM_OBJECT, ctypes.byref(prop), 0, None, ctypes.byref(size), devs
        )
        != 0
    ):
        return []
    return list(devs)


def find_device_by_name(name_substring: str) -> int | None:
    """Find a CoreAudio device ID by name substring (case-insensitive)."""
    needle = name_substring.lower()
    for d in list_device_ids():
        name = get_string_property(d, PROP_DEVICE_NAME)
        if name and needle in name.lower():
            return d
    return None


def get_default_output_device() -> tuple[int, str | None]:
    """Return (device_id, uid) for the current default output device."""
    prop = AudioObjectPropertyAddress(
        PROP_DEFAULT_OUTPUT,
        SCOPE_GLOBAL,
        ELEMENT_MAIN,
    )
    size = ctypes.c_uint32(4)
    dev_id = ctypes.c_uint32(0)
    _ca.AudioObjectGetPropertyData(
        SYSTEM_OBJECT,
        ctypes.byref(prop),
        0,
        None,
        ctypes.byref(size),
        ctypes.byref(dev_id),
    )
    uid = get_string_property(dev_id.value, PROP_DEVICE_UID)
    return dev_id.value, uid


# ---------------------------------------------------------------------------
# Aggregate device creation / destruction
# ---------------------------------------------------------------------------


def create_aggregate_device(desc: ctypes.c_void_p) -> int | None:
    """Create an aggregate device from a CFDictionary description.

    Returns the AudioObjectID on success, or None on failure.
    """
    dev_id = ctypes.c_uint32(0)
    status = _ca.AudioHardwareCreateAggregateDevice(desc, ctypes.byref(dev_id))
    if status != 0:
        log.warning("AudioHardwareCreateAggregateDevice failed: %d", status)
        return None
    return dev_id.value


def set_device_sample_rate(device_id: int, rate: float) -> bool:
    """Set the nominal sample rate on a CoreAudio device. Returns True on success."""
    prop = AudioObjectPropertyAddress(
        PROP_NOMINAL_SAMPLE_RATE, SCOPE_GLOBAL, ELEMENT_MAIN
    )
    val = ctypes.c_double(rate)
    status = _ca.AudioObjectSetPropertyData(
        device_id, ctypes.byref(prop), 0, None, ctypes.sizeof(val), ctypes.byref(val)
    )
    if status != 0:
        log.warning(
            "Failed to set sample rate to %g on device %d: %d", rate, device_id, status
        )
        return False
    return True


def set_device_buffer_size(device_id: int, frames: int) -> bool:
    """Set the buffer frame size on a CoreAudio device. Returns True on success."""
    prop = AudioObjectPropertyAddress(
        PROP_BUFFER_FRAME_SIZE, SCOPE_GLOBAL, ELEMENT_MAIN
    )
    val = ctypes.c_uint32(frames)
    status = _ca.AudioObjectSetPropertyData(
        device_id, ctypes.byref(prop), 0, None, ctypes.sizeof(val), ctypes.byref(val)
    )
    if status != 0:
        log.warning(
            "Failed to set buffer size to %d on device %d: %d",
            frames,
            device_id,
            status,
        )
        return False
    return True


def get_tap_format(tap_id: int) -> tuple[float, int] | None:
    """Read the AudioStreamBasicDescription from a Process Tap.

    Returns ``(sample_rate, channels)`` or ``None`` on failure.
    """
    import struct

    prop = AudioObjectPropertyAddress(PROP_TAP_FORMAT, SCOPE_GLOBAL, ELEMENT_MAIN)
    size = ctypes.c_uint32(0)
    if (
        _ca.AudioObjectGetPropertyDataSize(
            tap_id, ctypes.byref(prop), 0, None, ctypes.byref(size)
        )
        != 0
        or size.value < 40
    ):
        return None
    buf = (ctypes.c_char * size.value)()
    if (
        _ca.AudioObjectGetPropertyData(
            tap_id, ctypes.byref(prop), 0, None, ctypes.byref(size), buf
        )
        != 0
    ):
        return None
    raw = bytes(buf)
    sr = struct.unpack_from("<d", raw, 0)[0]
    ch = struct.unpack_from("<I", raw, 28)[0]
    return sr, ch


def destroy_aggregate_device(device_id: int) -> bool:
    """Destroy a previously-created aggregate device. Returns True on success."""
    status = _ca.AudioHardwareDestroyAggregateDevice(device_id)
    if status != 0:
        log.warning("AudioHardwareDestroyAggregateDevice failed: %d", status)
        return False
    return True
