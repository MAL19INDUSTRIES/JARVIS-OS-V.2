"""Shutdown-guard tests for JarvisUI.

The Gemini Live session runs on a worker thread and can report one last state
(or log one last line) while Qt is tearing down MainWindow on quit. Both
``JarvisUI.set_state`` and ``JarvisUI.write_log`` must swallow that teardown
race instead of raising ``RuntimeError: wrapped C/C++ object of type
MainWindow has been deleted``.

Runs headless: ``QT_QPA_PLATFORM=offscreen``.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6 import sip

import ui


class _NoSecrets:
    def get(self, _key):
        return None


class ShutdownGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(cls.temp_dir.name)
        cls.patchers = [
            patch.object(ui, "UI_SETTINGS_FILE", temp_path / "ui_settings.json"),
            patch.object(ui, "LAYOUT_SETTINGS_FILE", temp_path / "layout_settings.json"),
            patch.object(ui, "API_FILE", temp_path / "api_keys.json"),
            patch.object(ui, "get_secret_store", return_value=_NoSecrets()),
            patch.object(ui.MainWindow, "_setup_system_tray", lambda self: None),
            patch.object(ui.MainWindow, "_update_metrics", lambda self: None),
            patch.object(ui.MainWindow, "_restore_detached_panels", lambda self: None),
            patch.object(ui.MainWindow, "_start_layout_autosave", lambda self: None),
            patch.object(ui.MainWindow, "_start_auto_graphics_detection", lambda self: None),
        ]
        for patcher in cls.patchers:
            patcher.start()
        ui.UI_SETTINGS_FILE.write_text(
            json.dumps({"intro_completed": True, "intro_every_launch": False}),
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls):
        for patcher in cls.patchers:
            patcher.stop()
        cls.temp_dir.cleanup()

    def _make_ui(self):
        j = ui.JarvisUI("face.png")
        self.app.processEvents()
        self._ui = j
        return j

    def tearDown(self):
        j = getattr(self, "_ui", None)
        if j is not None:
            try:
                if not sip.isdeleted(j._win):
                    j._win.close()
            except Exception:
                pass

    def test_emits_silent_after_about_to_quit_and_window_deleted(self):
        """aboutToQuit marks closing; late emits from the worker are dropped."""
        j = self._make_ui()
        j._app.aboutToQuit.emit()
        self.assertTrue(j._closing)
        sip.delete(j._win)
        # Must not raise.
        j.set_state("LISTENING")
        j.write_log("SYS: test")

    def test_emits_silent_when_window_deleted_without_quit_flag(self):
        """Window gone before aboutToQuit: the sip.isdeleted() check swallows it."""
        j = self._make_ui()
        self.assertFalse(j._closing)
        sip.delete(j._win)
        # Must not raise.
        j.set_state("LISTENING")
        j.write_log("SYS: test")


if __name__ == "__main__":
    unittest.main()
