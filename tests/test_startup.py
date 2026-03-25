"""Startup smoke tests — catch failures that the rest of the suite misses.

These tests guard against the classes of failure that have burned us in
production but slipped past unit tests:

1. ModuleNotFoundError when running the `scarecrow` console script because
   the editable-install .pth file has the macOS UF_HIDDEN flag set.
2. 55-second startup delays because HF Hub makes network requests instead of
   loading cached models offline.
3. The console-script entry point (`main`) not being importable at all.
4. `Transcriber` not being constructable without crashing on import.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    fake_app._shutdown_summary = ""

    with (
        patch("scarecrow.transcriber.Transcriber", return_value=fake_transcriber),
        patch("scarecrow.app.ScarecrowApp", return_value=fake_app),
        patch("scarecrow.__main__._wait_for_enter_or_timeout"),
        patch("scarecrow.__main__._model_cache_path", return_value=None),
    ):
        __main__.main()

    fake_app.cleanup_after_exit.assert_called_once_with()


def test_main_module_importable_from_outside_project(tmp_path: Path) -> None:
    """Importing scarecrow.__main__.main must succeed when cwd is NOT the project root.

    Reproduces the Homebrew-Python / hidden-.pth bug: the editable install
    path was not on sys.path when launched from an arbitrary directory.
    """
    import subprocess

    from scarecrow.env_health import ensure_editable_install_visible

    ensure_editable_install_visible("scarecrow")

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
    assert t._worker is None


def test_transcriber_prepare_with_mocked_whisper() -> None:
    """prepare() must succeed when WhisperModel is mocked (tests the wiring)."""
    from scarecrow.transcriber import Transcriber

    with (
        patch("scarecrow.runtime.WhisperModel"),
        patch("scarecrow.transcriber._SileroVAD"),
    ):
        t = Transcriber()
        t.prepare()

    assert t.is_ready
    t.shutdown(timeout=0)


_REALTIME_MODEL_CACHED: bool | None = None


def _realtime_model_is_cached() -> bool:
    """Return True only if the realtime Whisper model is already on disk."""
    global _REALTIME_MODEL_CACHED
    if _REALTIME_MODEL_CACHED is None:
        from scarecrow import config
        from scarecrow.__main__ import _model_cache_path

        _REALTIME_MODEL_CACHED = _model_cache_path(config.REALTIME_MODEL) is not None
    return _REALTIME_MODEL_CACHED


import pytest  # noqa: E402  (after helpers so the flag check above works)


@pytest.mark.skipif(
    not _realtime_model_is_cached(),
    reason="realtime Whisper model not in HF cache — skipping live model load test",
)
def test_transcriber_prepare_with_real_model() -> None:
    """prepare() must succeed with the real Whisper model when it is cached.

    This is the most realistic startup smoke test — it exercises the actual
    model-loading path that runs when the user launches `scarecrow`.  If the
    model hangs for >30s the test will time out, catching a recurrence of the
    network-request regression.
    """
    import signal

    from scarecrow.transcriber import Transcriber

    def _timeout_handler(signum, frame):
        raise TimeoutError(
            "Transcriber.prepare() timed out after 30s — "
            "HF Hub may be making network requests despite HF_HUB_OFFLINE=1"
        )

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(30)  # 30s is generous; a cached load should finish in <5s
    try:
        t = Transcriber()
        t.prepare()
        assert t.is_ready
    finally:
        signal.alarm(0)
        if "t" in dir() and hasattr(t, "_worker"):
            t.shutdown()


# ---------------------------------------------------------------------------
# 5. .pth file not hidden (macOS UF_HIDDEN flag)
# ---------------------------------------------------------------------------

_UF_HIDDEN = 0x8000  # macOS chflags "hidden" bit


def _find_scarecrow_pth() -> Path | None:
    """Locate _scarecrow.pth in the active site-packages, if present."""
    import site

    for sp in site.getsitepackages():
        candidate = Path(sp) / "_scarecrow.pth"
        if candidate.exists():
            return candidate
    # Also check site.getusersitepackages() (editable installs in user env)
    user_site = Path(site.getusersitepackages())
    candidate = user_site / "_scarecrow.pth"
    if candidate.exists():
        return candidate
    return None


def test_pth_file_exists_in_site_packages() -> None:
    """_scarecrow.pth must exist in site-packages for the editable install to work.

    If this file is absent the `scarecrow` command will fail with
    ModuleNotFoundError when run outside the project directory.
    """
    pth = _find_scarecrow_pth()
    assert pth is not None, (
        "_scarecrow.pth not found in any site-packages directory.  "
        "Run `uv sync` or `pip install -e .` to create it."
    )


def test_pth_file_not_hidden() -> None:
    """_scarecrow.pth must NOT have the macOS UF_HIDDEN flag set.

    When macOS sets UF_HIDDEN on a .pth file, Python's site module silently
    skips it, so `import scarecrow` fails with ModuleNotFoundError from any
    directory other than the project root.

    This reproduces the exact failure mode that caused the app to crash on
    launch when installed via Homebrew Python.
    """
    pth = _find_scarecrow_pth()
    if pth is None:
        pytest.skip("_scarecrow.pth not found — run uv sync first")

    st_flags = os.stat(pth).st_flags
    assert (st_flags & _UF_HIDDEN) == 0, (
        f"{pth} has the macOS UF_HIDDEN flag set (st_flags={st_flags:#x}).  "
        "Run: chflags nohidden " + str(pth)
    )


def test_pth_file_points_to_project_root() -> None:
    """_scarecrow.pth must point to a directory that contains the scarecrow package."""
    pth = _find_scarecrow_pth()
    if pth is None:
        pytest.skip("_scarecrow.pth not found — run uv sync first")

    content = pth.read_text(encoding="utf-8").strip()
    project_path = Path(content)
    assert project_path.exists(), (
        f"_scarecrow.pth points to {content!r} which does not exist"
    )
    assert (project_path / "scarecrow").is_dir(), (
        f"_scarecrow.pth points to {content!r} but no scarecrow/ package found there"
    )
