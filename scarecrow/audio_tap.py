"""macOS Process Tap — capture system audio without BlackHole.

Uses the CoreAudio Process Tap API (macOS 14.2+) to capture the system
audio mix.  The tap is wrapped in a private aggregate device that
sounddevice can open as a normal input.

Requires **System Audio Recording** permission (Privacy & Security).
Without it, ``AudioHardwareCreateProcessTap`` succeeds but the stream
delivers silence.  The caller should detect this and warn the user.

Public API
----------
- ``create_system_tap() -> TapHandle | None``
- ``destroy_system_tap(handle: TapHandle) -> None``
"""

from __future__ import annotations

import atexit
import logging
import platform
import uuid
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Minimum macOS version for Process Tap API
_MIN_MACOS = (14, 2)

# Module-level safety net: tracks the active handle so atexit can clean up
_active_handle: TapHandle | None = None


@dataclass
class TapHandle:
    """Opaque handle for an active Process Tap + aggregate device."""

    tap_id: int  # AudioObjectID from AudioHardwareCreateProcessTap
    aggregate_id: int  # AudioObjectID from AudioHardwareCreateAggregateDevice
    device_index: int  # sounddevice device index


def _check_macos_version() -> bool:
    """Return True if the running macOS version supports Process Tap."""
    ver = platform.mac_ver()[0]
    if not ver:
        return False
    parts = ver.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return False
    return (major, minor) >= _MIN_MACOS


def _find_sounddevice_index(aggregate_id: int, name: str) -> int | None:
    """Find the sounddevice device index for our aggregate device.

    Matches by name since PortAudio doesn't expose AudioObjectIDs.
    If the device isn't in PortAudio's cached list (because it was created
    after sounddevice was first imported), force a PortAudio re-init.
    """
    import sounddevice as sd

    def _scan() -> int | None:
        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0 and dev.get("name", "") == name:
                return i
        return None

    idx = _scan()
    if idx is not None:
        return idx

    # Device not found — PortAudio cached its device list before the
    # aggregate was created.  Force a full re-init to pick up the new device.
    log.debug("Device '%s' not in PortAudio cache, forcing re-init", name)
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        log.debug("PortAudio re-init failed", exc_info=True)
        return None

    return _scan()


def create_system_tap() -> TapHandle | None:
    """Create a Process Tap capturing the global system audio mix.

    Returns a ``TapHandle`` on success, or ``None`` if the tap cannot be
    created (wrong macOS version, permission denied, missing PyObjC, etc.).
    The caller should treat ``None`` as "system audio unavailable" and
    degrade gracefully.
    """
    global _active_handle

    if not _check_macos_version():
        ver = platform.mac_ver()[0] or "unknown"
        log.warning(
            "System audio requires macOS %d.%d+ (Process Tap API). Current: %s",
            *_MIN_MACOS,
            ver,
        )
        return None

    try:
        import CoreAudio
    except ImportError:
        log.warning(
            "pyobjc-framework-CoreAudio not installed — system audio unavailable"
        )
        return None

    from scarecrow._coreaudio import (
        PROP_DEVICE_UID,
        cfarray,
        cfarray_append,
        cfdict,
        cfdict_set,
        cfnum,
        cfstr,
        create_aggregate_device,
        destroy_aggregate_device,
        get_default_output_device,
        get_string_property,
        get_tap_format,
        set_device_buffer_size,
        set_device_sample_rate,
    )

    # 1. Get the default output device UID (needed for fallback only)
    output_id, output_uid = get_default_output_device()
    if not output_uid:
        output_uid = get_string_property(output_id, PROP_DEVICE_UID)

    # 2. Create the CATapDescription via PyObjC
    tap_desc = (
        CoreAudio.CATapDescription.alloc().initStereoGlobalTapButExcludeProcesses_([])
    )
    tap_uuid = str(tap_desc.UUID())

    status, tap_id = CoreAudio.AudioHardwareCreateProcessTap(tap_desc, None)
    if status != 0:
        log.warning(
            "AudioHardwareCreateProcessTap failed (status %d) — "
            "check System Audio Recording permission in Privacy & Security",
            status,
        )
        return None

    log.info("Process Tap created (id=%d, uuid=%s)", tap_id, tap_uuid)

    # Check the tap's native format
    tap_fmt = get_tap_format(tap_id)
    if tap_fmt:
        log.info("Tap format: %.0f Hz, %d channels", *tap_fmt)

    # 3. Build the aggregate device description
    #    Tap-only aggregate — do NOT include sub-device list or master device.
    #    Including the output device as a sub-device causes a 3x sample rate
    #    mismatch where the device reports 48kHz but delivers at ~16kHz.
    #    Reference: Chromium catap_audio_input_stream.mm, Sunshine av_audio.mm
    agg_uid = f"com.scarecrow.tap-{uuid.uuid4().hex[:8]}"
    agg_name = "Scarecrow Tap"

    # Tap list: our process tap with drift compensation
    tap_entry = cfdict()
    cfdict_set(tap_entry, "uid", cfstr(tap_uuid))
    cfdict_set(tap_entry, "drift", cfnum(1))

    tap_list = cfarray()
    cfarray_append(tap_list, tap_entry)

    # Top-level aggregate description (tap only, no sub-devices)
    desc = cfdict()
    cfdict_set(desc, "uid", cfstr(agg_uid))
    cfdict_set(desc, "name", cfstr(agg_name))
    cfdict_set(desc, "private", cfnum(1))
    cfdict_set(desc, "taps", tap_list)

    aggregate_id = create_aggregate_device(desc)

    if aggregate_id is None:
        log.warning("Failed to create tap aggregate device")
        CoreAudio.AudioHardwareDestroyProcessTap(tap_id)
        return None

    log.info("Tap aggregate device created (id=%d, name=%s)", aggregate_id, agg_name)

    # Configure aggregate sample rate and buffer size BEFORE PortAudio sees it.
    # Without this, the aggregate may deliver at a wrong rate.
    target_rate = tap_fmt[0] if tap_fmt else 48000.0
    set_device_sample_rate(aggregate_id, target_rate)
    set_device_buffer_size(aggregate_id, 1024)

    # 4. Find the aggregate in sounddevice
    device_index = _find_sounddevice_index(aggregate_id, agg_name)
    if device_index is None:
        log.warning("Tap aggregate device not visible to sounddevice — falling back")
        destroy_aggregate_device(aggregate_id)
        CoreAudio.AudioHardwareDestroyProcessTap(tap_id)
        return None

    log.info("Tap aggregate visible as sounddevice index %d", device_index)

    handle = TapHandle(
        tap_id=tap_id,
        aggregate_id=aggregate_id,
        device_index=device_index,
    )
    _active_handle = handle
    atexit.register(_atexit_cleanup)
    return handle


def destroy_system_tap(handle: TapHandle) -> None:
    """Tear down a Process Tap and its aggregate device."""
    global _active_handle

    try:
        import CoreAudio
    except ImportError:
        log.warning("Cannot import CoreAudio for tap cleanup")
        return

    from scarecrow._coreaudio import destroy_aggregate_device

    # Destroy aggregate first, then tap
    destroy_aggregate_device(handle.aggregate_id)
    CoreAudio.AudioHardwareDestroyProcessTap(handle.tap_id)
    log.info(
        "Process Tap destroyed (tap=%d, aggregate=%d)",
        handle.tap_id,
        handle.aggregate_id,
    )

    if _active_handle is handle:
        _active_handle = None


def _atexit_cleanup() -> None:
    """Safety net: destroy tap on process exit."""
    if _active_handle is not None:
        import contextlib

        with contextlib.suppress(Exception):
            destroy_system_tap(_active_handle)
