"""macOS audio routing — create/destroy Multi-Output Devices via CoreAudio.

When --sys-audio is used, Scarecrow creates a private Multi-Output Device
that combines the current system output (speakers/headphones) with BlackHole.
This routes system audio to BlackHole automatically so the user doesn't need
to manually configure Audio MIDI Setup.

On shutdown, the original default output is restored and the device is destroyed.
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import ctypes.util
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CoreAudio / CoreFoundation via ctypes
# ---------------------------------------------------------------------------

_ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))
_cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))

# CoreAudio constants
_SYSTEM_OBJECT = 1
_PROP_DEVICES = int.from_bytes(b"dev#", "big")
_PROP_DEFAULT_OUTPUT = int.from_bytes(b"dOut", "big")
_SCOPE_GLOBAL = int.from_bytes(b"glob", "big")
_ELEMENT_MAIN = 0
_PROP_DEVICE_UID = int.from_bytes(b"uid ", "big")
_PROP_DEVICE_NAME = int.from_bytes(b"lnam", "big")

# CoreFoundation constants
_CF_UTF8 = 0x08000100
_CF_INT32_TYPE = 9
_CF_ALLOC = None


class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


# CF function signatures
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

# CA function signatures
_ca.AudioObjectGetPropertyDataSize.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(_AudioObjectPropertyAddress),
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
]
_ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
_ca.AudioObjectGetPropertyData.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(_AudioObjectPropertyAddress),
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_void_p,
]
_ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
_ca.AudioObjectSetPropertyData.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(_AudioObjectPropertyAddress),
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

# CF callback structs (global pointers)
_CF_ARRAY_CBS = ctypes.c_void_p.in_dll(_cf, "kCFTypeArrayCallBacks")
_CF_DICT_KEY_CBS = ctypes.c_void_p.in_dll(_cf, "kCFTypeDictionaryKeyCallBacks")
_CF_DICT_VAL_CBS = ctypes.c_void_p.in_dll(_cf, "kCFTypeDictionaryValueCallBacks")


# ---------------------------------------------------------------------------
# CF helpers
# ---------------------------------------------------------------------------


def _cfstr(s: str) -> ctypes.c_void_p:
    return _cf.CFStringCreateWithCString(_CF_ALLOC, s.encode("utf-8"), _CF_UTF8)


def _cfnum(n: int) -> ctypes.c_void_p:
    val = ctypes.c_int32(n)
    return _cf.CFNumberCreate(_CF_ALLOC, _CF_INT32_TYPE, ctypes.byref(val))


def _cfdict() -> ctypes.c_void_p:
    return _cf.CFDictionaryCreateMutable(
        _CF_ALLOC,
        0,
        _CF_DICT_KEY_CBS,
        _CF_DICT_VAL_CBS,
    )


def _cfarray() -> ctypes.c_void_p:
    return _cf.CFArrayCreateMutable(_CF_ALLOC, 0, _CF_ARRAY_CBS)


# ---------------------------------------------------------------------------
# CoreAudio helpers
# ---------------------------------------------------------------------------


def _get_string_property(device_id: int, selector: int) -> str | None:
    prop = _AudioObjectPropertyAddress(selector, _SCOPE_GLOBAL, _ELEMENT_MAIN)
    size = ctypes.c_uint32(0)
    if (
        _ca.AudioObjectGetPropertyDataSize(
            device_id, ctypes.byref(prop), 0, None, ctypes.byref(size)
        )
        != 0
    ):
        return None
    cfstr = ctypes.c_void_p()
    if (
        _ca.AudioObjectGetPropertyData(
            device_id,
            ctypes.byref(prop),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(cfstr),
        )
        != 0
    ):
        return None
    buf = ctypes.create_string_buffer(256)
    if _cf.CFStringGetCString(cfstr, buf, 256, _CF_UTF8):
        return buf.value.decode("utf-8")
    return None


def get_default_output_device() -> tuple[int, str | None]:
    """Return (device_id, uid) for the current default output device."""
    prop = _AudioObjectPropertyAddress(
        _PROP_DEFAULT_OUTPUT,
        _SCOPE_GLOBAL,
        _ELEMENT_MAIN,
    )
    size = ctypes.c_uint32(4)
    dev_id = ctypes.c_uint32(0)
    _ca.AudioObjectGetPropertyData(
        _SYSTEM_OBJECT,
        ctypes.byref(prop),
        0,
        None,
        ctypes.byref(size),
        ctypes.byref(dev_id),
    )
    uid = _get_string_property(dev_id.value, _PROP_DEVICE_UID)
    return dev_id.value, uid


def find_device_uid(name_substring: str) -> str | None:
    """Find a CoreAudio device UID by name substring (case-insensitive)."""
    prop = _AudioObjectPropertyAddress(
        _PROP_DEVICES,
        _SCOPE_GLOBAL,
        _ELEMENT_MAIN,
    )
    size = ctypes.c_uint32(0)
    _ca.AudioObjectGetPropertyDataSize(
        _SYSTEM_OBJECT, ctypes.byref(prop), 0, None, ctypes.byref(size)
    )
    n = size.value // 4
    devs = (ctypes.c_uint32 * n)()
    _ca.AudioObjectGetPropertyData(
        _SYSTEM_OBJECT,
        ctypes.byref(prop),
        0,
        None,
        ctypes.byref(size),
        devs,
    )
    needle = name_substring.lower()
    for d in devs:
        name = _get_string_property(d, _PROP_DEVICE_NAME)
        if name and needle in name.lower():
            return _get_string_property(d, _PROP_DEVICE_UID)
    return None


def _set_default_output(device_id: int) -> bool:
    """Set the system default output device. Returns True on success."""
    prop = _AudioObjectPropertyAddress(
        _PROP_DEFAULT_OUTPUT,
        _SCOPE_GLOBAL,
        _ELEMENT_MAIN,
    )
    dev = ctypes.c_uint32(device_id)
    status = _ca.AudioObjectSetPropertyData(
        _SYSTEM_OBJECT, ctypes.byref(prop), 0, None, 4, ctypes.byref(dev)
    )
    return status == 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class MultiOutputDevice:
    """Handle for a programmatically created Multi-Output Device."""

    aggregate_id: int
    original_output_id: int
    original_output_uid: str | None


def create_multi_output(
    blackhole_name: str = "BlackHole",
) -> MultiOutputDevice | None:
    """Create a Multi-Output Device routing audio to speakers + BlackHole.

    Returns a MultiOutputDevice handle, or None if creation failed.
    The device is set as the default output. Call destroy_multi_output()
    on shutdown to restore the original output.

    An atexit handler is registered as a safety net for SIGTERM/normal exit.
    """
    # Find BlackHole UID
    bh_uid = find_device_uid(blackhole_name)
    if bh_uid is None:
        log.info("Cannot create multi-output: %s not found", blackhole_name)
        return None

    # Get current output device
    orig_id, orig_uid = get_default_output_device()
    if orig_uid is None:
        log.warning("Cannot determine current output device UID")
        return None

    # Don't create if the current output is already an aggregate containing BlackHole
    orig_name = _get_string_property(orig_id, _PROP_DEVICE_NAME)
    if orig_name and "scarecrow" in orig_name.lower():
        log.info("Scarecrow multi-output already active")
        return None

    # Build sub-device array
    sub_devices = _cfarray()
    for uid in [orig_uid, bh_uid]:
        sub = _cfdict()
        _cf.CFDictionarySetValue(sub, _cfstr("uid"), _cfstr(uid))
        _cf.CFArrayAppendValue(sub_devices, sub)

    # Build aggregate device description
    desc = _cfdict()
    _cf.CFDictionarySetValue(desc, _cfstr("uid"), _cfstr("com.scarecrow.multioutput"))
    _cf.CFDictionarySetValue(desc, _cfstr("name"), _cfstr("Scarecrow Output"))
    _cf.CFDictionarySetValue(desc, _cfstr("subdevices"), sub_devices)
    _cf.CFDictionarySetValue(desc, _cfstr("master"), _cfstr(orig_uid))
    _cf.CFDictionarySetValue(desc, _cfstr("private"), _cfnum(1))
    _cf.CFDictionarySetValue(desc, _cfstr("stacked"), _cfnum(0))

    # Create the aggregate device
    agg_id = ctypes.c_uint32(0)
    status = _ca.AudioHardwareCreateAggregateDevice(desc, ctypes.byref(agg_id))
    if status != 0:
        log.warning("AudioHardwareCreateAggregateDevice failed: OSStatus %d", status)
        return None

    # Set as default output
    if not _set_default_output(agg_id.value):
        log.warning("Failed to set multi-output as default; destroying")
        _ca.AudioHardwareDestroyAggregateDevice(agg_id.value)
        return None

    handle = MultiOutputDevice(
        aggregate_id=agg_id.value,
        original_output_id=orig_id,
        original_output_uid=orig_uid,
    )

    # Safety net: restore on process exit (covers SIGTERM, normal exit)
    atexit.register(_atexit_cleanup, handle)

    log.info(
        "Created multi-output device (ID=%d) routing to %s + %s",
        agg_id.value,
        orig_name or orig_uid,
        blackhole_name,
    )
    return handle


def destroy_multi_output(handle: MultiOutputDevice) -> None:
    """Restore original output and destroy the Multi-Output Device."""
    # Restore original default output
    if not _set_default_output(handle.original_output_id):
        log.warning("Failed to restore original output device")

    # Destroy the aggregate
    status = _ca.AudioHardwareDestroyAggregateDevice(handle.aggregate_id)
    if status != 0:
        log.warning("AudioHardwareDestroyAggregateDevice failed: OSStatus %d", status)
    else:
        log.info("Destroyed multi-output device (ID=%d)", handle.aggregate_id)


def _atexit_cleanup(handle: MultiOutputDevice) -> None:
    """Safety net: restore output on process exit."""
    with contextlib.suppress(Exception):
        destroy_multi_output(handle)
