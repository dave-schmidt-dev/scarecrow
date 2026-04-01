"""Unit tests for audio_routing.activate_scarecrow_output() fallback behaviour.

Covers the bug fix where activate_scarecrow_output() calls _find_builtin_output()
when the system is already set to the Scarecrow device (e.g. after a crash), so
that the restored device on exit is the real speaker rather than Scarecrow itself.
"""

from __future__ import annotations

from unittest.mock import patch

from scarecrow.audio_routing import activate_scarecrow_output

_SCARECROW_ID = 10
_BUILTIN_ID = 5
_OTHER_ID = 7


# ---------------------------------------------------------------------------
# Already on Scarecrow Output — built-in available
# ---------------------------------------------------------------------------


def test_already_on_scarecrow_with_builtin_uses_builtin_as_original() -> None:
    """When current output IS the Scarecrow device and a built-in exists,
    original_output_id should be the built-in device, not the Scarecrow device."""
    with (
        patch(
            "scarecrow.audio_routing.find_device_by_name",
            return_value=_SCARECROW_ID,
        ),
        patch(
            "scarecrow.audio_routing.get_default_output_device",
            return_value=(_SCARECROW_ID, "ScarecrowUID"),
        ),
        patch(
            "scarecrow.audio_routing._find_builtin_output",
            return_value=_BUILTIN_ID,
        ),
        patch(
            "scarecrow.audio_routing._get_string_property",
            return_value="Built-in Output",
        ),
        patch("scarecrow.audio_routing._set_default_output", return_value=True),
        patch("scarecrow.audio_routing.atexit.register"),
    ):
        handle = activate_scarecrow_output("Scarecrow Output")

    assert handle is not None
    assert handle.original_output_id == _BUILTIN_ID
    assert handle.scarecrow_output_id == _SCARECROW_ID


# ---------------------------------------------------------------------------
# Already on Scarecrow Output — no built-in available
# ---------------------------------------------------------------------------


def test_already_on_scarecrow_without_builtin_falls_back_to_scarecrow_id() -> None:
    """When current output IS the Scarecrow device and no built-in exists,
    original_output_id falls back to the Scarecrow device id (legacy behaviour)."""
    with (
        patch(
            "scarecrow.audio_routing.find_device_by_name",
            return_value=_SCARECROW_ID,
        ),
        patch(
            "scarecrow.audio_routing.get_default_output_device",
            return_value=(_SCARECROW_ID, "ScarecrowUID"),
        ),
        patch(
            "scarecrow.audio_routing._find_builtin_output",
            return_value=None,
        ),
        patch(
            "scarecrow.audio_routing._get_string_property",
            return_value=None,
        ),
        patch("scarecrow.audio_routing._set_default_output", return_value=True),
        patch("scarecrow.audio_routing.atexit.register"),
    ):
        handle = activate_scarecrow_output("Scarecrow Output")

    assert handle is not None
    assert handle.original_output_id == _SCARECROW_ID
    assert handle.scarecrow_output_id == _SCARECROW_ID


# ---------------------------------------------------------------------------
# Normal path — current output is something else entirely
# ---------------------------------------------------------------------------


def test_normal_path_stores_previous_output_as_original() -> None:
    """When current output is NOT the Scarecrow device, original_output_id is
    the device that was active before the switch."""
    with (
        patch(
            "scarecrow.audio_routing.find_device_by_name",
            return_value=_SCARECROW_ID,
        ),
        patch(
            "scarecrow.audio_routing.get_default_output_device",
            return_value=(_OTHER_ID, "OtherUID"),
        ),
        patch(
            "scarecrow.audio_routing._get_string_property",
            return_value="MacBook Pro Speakers",
        ),
        patch(
            "scarecrow.audio_routing._set_default_output",
            return_value=True,
        ),
        patch("scarecrow.audio_routing.atexit.register"),
    ):
        handle = activate_scarecrow_output("Scarecrow Output")

    assert handle is not None
    assert handle.original_output_id == _OTHER_ID
    assert handle.scarecrow_output_id == _SCARECROW_ID
