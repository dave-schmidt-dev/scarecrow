"""Startup smoke tests — catch failures that the rest of the suite misses.

These tests guard against the classes of failure that have burned us in
production but slipped past unit tests:

1. 55-second startup delays because HF Hub makes network requests instead of
   loading cached models offline.
2. The console-script entry point (`main`) not being importable at all.
3. `Transcriber` not being constructable without crashing on import.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Package importability
# ---------------------------------------------------------------------------


def test_scarecrow_package_is_importable() -> None:
    """The top-level `scarecrow` package must be importable."""
    import scarecrow  # noqa: F401


def test_scarecrow_config_is_importable() -> None:
    """scarecrow.config must be importable (required by almost every module)."""
    import scarecrow.config  # noqa: F401


def test_scarecrow_transcriber_is_importable() -> None:
    """scarecrow.transcriber must be importable without loading any models."""
    import scarecrow.transcriber  # noqa: F401


def test_scarecrow_app_is_importable() -> None:
    """scarecrow.app must be importable without launching any UI."""
    import scarecrow.app  # noqa: F401


def test_scarecrow_recorder_does_not_import_sounddevice() -> None:
    """Importing scarecrow.recorder must not trigger sounddevice import.

    sounddevice initializes PortAudio at import time, spawning CoreAudio
    background threads that crash the interpreter during test-suite teardown.
    recorder.py must use lazy imports — sounddevice only loads inside
    AudioRecorder.start(), never at module level.
    """
    import scarecrow.recorder

    assert not hasattr(scarecrow.recorder, "sd"), (
        "scarecrow.recorder must not bind 'sd' at module level"
    )


# ---------------------------------------------------------------------------
# 2. Console-script entry point
# ---------------------------------------------------------------------------


def test_main_function_is_importable() -> None:
    """scarecrow.__main__.main must exist and be callable.

    This is the function wired to the `scarecrow` console script in
    pyproject.toml.  If it can't be imported, the script entry point crashes
    before printing a single line.
    """
    from scarecrow.__main__ import main

    assert callable(main)


def test_main_finally_uses_app_cleanup_hook() -> None:
    """The entrypoint finally block must delegate shutdown to the app cleanup hook."""
    from scarecrow import __main__

    fake_transcriber = MagicMock()
    fake_transcriber.prepare.return_value = None
    fake_app = MagicMock()
    fake_app.run.side_effect = KeyboardInterrupt()
    fake_app._discard_mode = False
    fake_app._shutdown_summary = ""

    with (
        patch("scarecrow.transcriber.Transcriber", return_value=fake_transcriber),
        patch("scarecrow.app.ScarecrowApp", return_value=fake_app),
        patch("scarecrow.__main__._wait_for_enter_or_timeout"),
    ):
        __main__.main()

    fake_app.cleanup_after_exit.assert_called_once_with()
    fake_app.post_exit_cleanup.assert_called_once_with()


def test_main_calls_preload_batch_model() -> None:
    """main() must call transcriber.preload_batch_model() during startup."""
    from scarecrow import __main__

    fake_transcriber = MagicMock()
    fake_transcriber.prepare.return_value = None
    fake_transcriber.preload_batch_model.return_value = None
    fake_app = MagicMock()
    fake_app.run.side_effect = KeyboardInterrupt()
    fake_app._shutdown_summary = ""

    with (
        patch("scarecrow.transcriber.Transcriber", return_value=fake_transcriber),
        patch("scarecrow.app.ScarecrowApp", return_value=fake_app),
        patch("scarecrow.__main__._wait_for_enter_or_timeout"),
    ):
        __main__.main()

    fake_transcriber.preload_batch_model.assert_called_once()


@pytest.mark.skip(
    reason="Python 3.12.13 skips all .pth files in venv site-packages, "
    "breaking uv editable installs from non-project cwd"
)
def test_main_module_importable_from_outside_project(tmp_path: Path) -> None:
    """scarecrow.__main__.main must import when cwd is not the project root."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", "from scarecrow.__main__ import main; main.__name__"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, (
        f"Import of scarecrow.__main__.main failed from {tmp_path}:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 3. HF Hub offline flags set at module level
# ---------------------------------------------------------------------------


def test_hf_hub_offline_set_by_main_module() -> None:
    """HF_HUB_OFFLINE must be set to '1' by __main__ module-level code.

    Without this, HuggingFace Hub tries to fetch model metadata from the
    network on every startup, adding 30-60s of delay even when models are
    cached locally.
    """
    import importlib

    importlib.import_module("scarecrow.__main__")
    assert os.environ.get("HF_HUB_OFFLINE") == "1", (
        "HF_HUB_OFFLINE must be set to '1' at __main__ module level to prevent "
        "network requests during model loading"
    )


def test_hf_hub_disable_implicit_token_set_by_main_module() -> None:
    """HF_HUB_DISABLE_IMPLICIT_TOKEN must be set by __main__ module-level code."""
    import importlib

    importlib.import_module("scarecrow.__main__")
    assert os.environ.get("HF_HUB_DISABLE_IMPLICIT_TOKEN") == "1"


# ---------------------------------------------------------------------------
# 4. Transcriber construction
# ---------------------------------------------------------------------------


def test_transcriber_can_be_instantiated() -> None:
    """Transcriber() must construct without raising or loading models.

    Catches import-time errors (missing deps, ONNX model not found, etc.)
    that would crash the app before prepare() is even called.
    """
    from scarecrow.transcriber import Transcriber

    t = Transcriber()
    assert not t.is_ready


def test_main_handles_preload_batch_model_failure() -> None:
    """main() must handle preload_batch_model() failures with a clean exit."""
    from scarecrow import __main__

    fake_transcriber = MagicMock()
    fake_transcriber.prepare.return_value = None
    fake_transcriber.preload_batch_model.side_effect = RuntimeError("MLX OOM")

    with (
        patch("scarecrow.transcriber.Transcriber", return_value=fake_transcriber),
        patch("scarecrow.__main__._wait_for_enter_or_timeout"),
        pytest.raises(SystemExit) as exc_info,
    ):
        __main__.main()

    assert exc_info.value.code == 1


@pytest.mark.integration
@pytest.mark.skipif(
    not importlib.util.find_spec("parakeet_mlx"),
    reason="parakeet_mlx not installed",
)
def test_transcriber_prepare_with_real_model() -> None:
    """prepare() must succeed and preload_batch_model works when parakeet_mlx available.

    This is the most realistic startup smoke test — it exercises the actual
    model-loading path that runs when the user launches `scarecrow`.
    """

    from scarecrow.transcriber import Transcriber

    t = Transcriber()
    t.prepare()
    assert t.is_ready

    # preload_batch_model should not raise when parakeet_mlx is available
    t.preload_batch_model()
    t.shutdown()
