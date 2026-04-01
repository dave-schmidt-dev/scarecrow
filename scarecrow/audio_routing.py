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
_PROP_TRANSPORT_TYPE = int.from_bytes(b"tran", "big")

# kAudioDeviceTransportType values
_TRANSPORT_BUILTIN = int.from_bytes(b"bltn", "big")
_TRANSPORT_HDMI = int.from_bytes(b"hdmi", "big")
_TRANSPORT_DISPLAYPORT = int.from_bytes(b"dprt", "big")

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


def _get_uint32_property(device_id: int, selector: int) -> int | None:
    prop = _AudioObjectPropertyAddress(selector, _SCOPE_GLOBAL, _ELEMENT_MAIN)
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


def _is_display_output(device_id: int) -> bool:
    """Return True if the device is an HDMI or DisplayPort output.

    macOS Continuity calls sometimes hijack the default output to a display
    audio device. We skip those when saving the 'original' output so that
    Scarecrow restores the real listening device on shutdown.
    """
    transport = _get_uint32_property(device_id, _PROP_TRANSPORT_TYPE)
    return transport in (_TRANSPORT_HDMI, _TRANSPORT_DISPLAYPORT)


def _find_builtin_output() -> int | None:
    """Return the device_id of the first built-in output device, or None."""
    prop = _AudioObjectPropertyAddress(_PROP_DEVICES, _SCOPE_GLOBAL, _ELEMENT_MAIN)
    size = ctypes.c_uint32(0)
    _ca.AudioObjectGetPropertyDataSize(
        _SYSTEM_OBJECT, ctypes.byref(prop), 0, None, ctypes.byref(size)
    )
    n = size.value // 4
    devs = (ctypes.c_uint32 * n)()
    _ca.AudioObjectGetPropertyData(
        _SYSTEM_OBJECT, ctypes.byref(prop), 0, None, ctypes.byref(size), devs
    )
    for d in devs:
        transport = _get_uint32_property(d, _PROP_TRANSPORT_TYPE)
        if transport == _TRANSPORT_BUILTIN:
            # Confirm it has output channels
            name = _get_string_property(d, _PROP_DEVICE_NAME)
            if name:
                return d
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
# Public API — switch to/from a persistent Multi-Output Device
# ---------------------------------------------------------------------------


def find_device_by_name(name_substring: str) -> int | None:
    """Find a CoreAudio device ID by name substring (case-insensitive)."""
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
            return d
    return None


@dataclass
class AudioOutputSwitch:
    """Handle for an output device switch (restore on exit)."""

    original_output_id: int
    scarecrow_output_id: int


_active_switch: AudioOutputSwitch | None = None


def activate_scarecrow_output(
    device_name: str = "Scarecrow Output",
) -> AudioOutputSwitch | None:
    """Switch default output to the Scarecrow Multi-Output Device.

    The device must already exist (created via Audio MIDI Setup).
    Returns a handle for restore_output(), or None if the device
    wasn't found or is already active. An atexit handler is registered
    as a safety net.
    """
    global _active_switch

    scarecrow_id = find_device_by_name(device_name)
    if scarecrow_id is None:
        log.info("'%s' device not found — sys audio routing unavailable", device_name)
        return None

    orig_id, _ = get_default_output_device()
    orig_name = _get_string_property(orig_id, _PROP_DEVICE_NAME)

    # Already on Scarecrow Output — nothing to do
    if orig_id == scarecrow_id:
        log.info("Already using '%s'", device_name)
        handle = AudioOutputSwitch(
            original_output_id=orig_id,
            scarecrow_output_id=scarecrow_id,
        )
        _active_switch = handle
        atexit.register(_atexit_restore)
        return handle

    if not _set_default_output(scarecrow_id):
        log.warning("Failed to switch output to '%s'", device_name)
        return None

    handle = AudioOutputSwitch(
        original_output_id=orig_id,
        scarecrow_output_id=scarecrow_id,
    )
    _active_switch = handle
    atexit.register(_atexit_restore)

    log.info(
        "Switched output: %s → %s",
        orig_name or f"device {orig_id}",
        device_name,
    )
    return handle


def restore_output(handle: AudioOutputSwitch) -> None:
    """Restore the original output device."""
    global _active_switch

    # Don't restore if the original was already Scarecrow Output
    if handle.original_output_id == handle.scarecrow_output_id:
        _active_switch = None
        return

    if _set_default_output(handle.original_output_id):
        orig_name = _get_string_property(handle.original_output_id, _PROP_DEVICE_NAME)
        log.info(
            "Restored output: %s", orig_name or f"device {handle.original_output_id}"
        )
    else:
        log.warning("Failed to restore original output device")

    _active_switch = None


def _atexit_restore() -> None:
    """Safety net: restore output on process exit."""
    if _active_switch is not None:
        with contextlib.suppress(Exception):
            restore_output(_active_switch)
