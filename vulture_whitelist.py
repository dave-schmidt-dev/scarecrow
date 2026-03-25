"""Whitelist dynamic entry points and test-only compatibility shims for vulture."""

from scarecrow.app import InfoBar, ScarecrowApp
from scarecrow.env_health import (
    ensure_editable_install_visible,
    has_hidden_flag,
    verify_import_outside_project,
)
from scarecrow.live_captioner import LiveCaptioner
from scarecrow.recorder import AudioRecorder

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
    ScarecrowApp.on_unmount,
    AudioRecorder.is_recording,
    AudioRecorder.is_paused,
    AudioRecorder.peak_level,
    LiveCaptioner._task,
    LiveCaptioner._prev_text,
    has_hidden_flag,
    ensure_editable_install_visible,
    verify_import_outside_project,
)
