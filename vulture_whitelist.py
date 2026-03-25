"""Whitelist dynamic entry points and test-only compatibility shims for vulture."""

from scarecrow.app import InfoBar, ScarecrowApp
from scarecrow.env_health import (
    ensure_editable_install_visible,
    has_hidden_flag,
    verify_import_outside_project,
)
from scarecrow.recorder import AudioRecorder
from scarecrow.transcriber import Transcriber, _SileroVAD

_ = (
    InfoBar.render,
    ScarecrowApp.TITLE,
    ScarecrowApp.CSS_PATH,
    ScarecrowApp.ENABLE_COMMAND_PALETTE,
    ScarecrowApp.BINDINGS,
    ScarecrowApp.compose,
    ScarecrowApp.on_mount,
    ScarecrowApp.watch_state,
    ScarecrowApp._update_live_partial,
    ScarecrowApp.action_pause,
    ScarecrowApp.action_quit,
    ScarecrowApp.update_live_preview,
    ScarecrowApp.append_caption,
    AudioRecorder.is_recording,
    AudioRecorder.is_paused,
    AudioRecorder.peak_level,
    ScarecrowApp.on_unmount,
    Transcriber.set_callbacks,
    Transcriber.feed_audio,
    _SileroVAD.__del__,
    has_hidden_flag,
    ensure_editable_install_visible,
    verify_import_outside_project,
)
