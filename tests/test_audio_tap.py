"""Unit tests for audio_tap — Process Tap lifecycle with mocked CoreAudio."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scarecrow.audio_tap import TapHandle, _check_macos_version, destroy_system_tap

# ---------------------------------------------------------------------------
# macOS version gate
# ---------------------------------------------------------------------------


@patch(
    "scarecrow.audio_tap.platform.mac_ver", return_value=("14.2.1", ("", "", ""), "")
)
def test_version_gate_passes_14_2(mock_ver: MagicMock) -> None:
    assert _check_macos_version() is True


@patch("scarecrow.audio_tap.platform.mac_ver", return_value=("14.1", ("", "", ""), ""))
def test_version_gate_fails_14_1(mock_ver: MagicMock) -> None:
    assert _check_macos_version() is False


@patch("scarecrow.audio_tap.platform.mac_ver", return_value=("15.0", ("", "", ""), ""))
def test_version_gate_passes_15(mock_ver: MagicMock) -> None:
    assert _check_macos_version() is True


@patch("scarecrow.audio_tap.platform.mac_ver", return_value=("26.4", ("", "", ""), ""))
def test_version_gate_passes_26(mock_ver: MagicMock) -> None:
    assert _check_macos_version() is True


@patch("scarecrow.audio_tap.platform.mac_ver", return_value=("", ("", "", ""), ""))
def test_version_gate_fails_empty(mock_ver: MagicMock) -> None:
    assert _check_macos_version() is False


# ---------------------------------------------------------------------------
# create_system_tap() — version gate
# ---------------------------------------------------------------------------


@patch("scarecrow.audio_tap._check_macos_version", return_value=False)
def test_create_returns_none_on_old_macos(mock_ver: MagicMock) -> None:
    from scarecrow.audio_tap import create_system_tap

    assert create_system_tap() is None


# ---------------------------------------------------------------------------
# create_system_tap() — PyObjC import failure
# ---------------------------------------------------------------------------


@patch("scarecrow.audio_tap._check_macos_version", return_value=True)
def test_create_returns_none_when_pyobjc_missing(mock_ver: MagicMock) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "CoreAudio":
            raise ImportError("no CoreAudio")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        from scarecrow.audio_tap import create_system_tap

        assert create_system_tap() is None


# ---------------------------------------------------------------------------
# destroy_system_tap()
# ---------------------------------------------------------------------------


def test_destroy_cleans_up() -> None:
    """destroy_system_tap calls DestroyProcessTap and destroy_aggregate_device."""
    handle = TapHandle(tap_id=42, aggregate_id=99, device_index=6)

    mock_ca = MagicMock()
    mock_destroy_agg = MagicMock(return_value=True)

    with (
        patch.dict("sys.modules", {"CoreAudio": mock_ca}),
        patch("scarecrow._coreaudio.destroy_aggregate_device", mock_destroy_agg),
    ):
        destroy_system_tap(handle)

    mock_destroy_agg.assert_called_once_with(99)
    mock_ca.AudioHardwareDestroyProcessTap.assert_called_once_with(42)
