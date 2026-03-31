"""Whitelist dynamic entry points and test-only compatibility shims for vulture."""

from scarecrow.app import InfoBar, ScarecrowApp
from scarecrow.recorder import AudioRecorder
from scarecrow.sys_audio import SystemAudioCapture, find_blackhole_device

_ = (
    InfoBar.render,
    ScarecrowApp.TITLE,
    ScarecrowApp.CSS_PATH,
    ScarecrowApp.ENABLE_COMMAND_PALETTE,
    ScarecrowApp.BINDINGS,
    ScarecrowApp.compose,
    ScarecrowApp.on_mount,
    ScarecrowApp.watch_state,
    ScarecrowApp.action_pause,
    ScarecrowApp.action_quit,
    ScarecrowApp.on_input_submitted,
    ScarecrowApp._NOTE_PREFIXES,
    ScarecrowApp.on_unmount,
    AudioRecorder.is_recording,
    AudioRecorder.is_paused,
    AudioRecorder.peak_level,
    SystemAudioCapture,
    find_blackhole_device,
)
