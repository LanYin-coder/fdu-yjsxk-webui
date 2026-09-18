import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import desktop
import webui


class PlatformTests(unittest.TestCase):
    def test_data_paths_preserve_mac_and_use_windows_local_appdata(self):
        self.assertEqual(desktop.data_directory("/app", True, "darwin", {}, "/user"),
                         Path("/user/Library/Application Support/FDU选课助手"))
        self.assertEqual(desktop.data_directory("/app", True, "win32", {"LOCALAPPDATA": "/local"}, "/user"),
                         Path("/local/FDUCourseHelper"))
        self.assertEqual(desktop.data_directory("/source", False, "win32", {}, "/user"), Path("/source"))

    def test_windows_launch_has_no_console_and_stop_does_not_use_posix_signals(self):
        self.assertEqual(desktop.process_options("win32")["creationflags"], 0x08000200)
        child = mock.Mock()
        child.poll.return_value = None
        with mock.patch.object(desktop.os, "killpg", create=True) as killpg:
            desktop.terminate_worker(child, platform="win32")
            child.terminate.assert_called_once()
            desktop.terminate_worker(child, force=True, platform="win32")
            child.kill.assert_called_once()
            killpg.assert_not_called()

    def test_windows_sleep_inhibition_is_restored_on_error(self):
        api = mock.Mock()
        with mock.patch.object(desktop.sys, "platform", "win32"), mock.patch.object(desktop.ctypes, "windll", api, create=True):
            with self.assertRaises(ValueError):
                with desktop.keep_awake():
                    raise ValueError("test")
        self.assertEqual(api.kernel32.SetThreadExecutionState.call_args_list,
                         [mock.call(0x80000001), mock.call(0x80000000)])

    def test_instance_lock_does_not_delete_another_instances_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = desktop.LocalInstance(directory), desktop.LocalInstance(directory)
            self.assertTrue(first.acquire())
            try:
                first.publish(18765)
                self.assertFalse(second.acquire())
                second.close()
                self.assertTrue(first.metadata.exists())
            finally:
                first.close()
            self.assertFalse(first.metadata.exists())
            self.assertTrue(second.acquire())
            second.close()

    def test_stale_metadata_does_not_block_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "instance.json").write_text('{"port": 11111}', encoding="utf-8")
            instance = desktop.LocalInstance(directory)
            self.assertTrue(instance.acquire())
            instance.publish(18765)
            self.assertEqual(json.loads(instance.metadata.read_text())["port"], 18765)
            instance.close()

    def test_missing_or_old_webview2_uses_browser_fallback(self):
        registry = mock.MagicMock()
        registry.KEY_READ, registry.KEY_WOW64_32KEY, registry.KEY_WOW64_64KEY = 1, 2, 4
        with mock.patch.dict(sys.modules, {"winreg": registry}):
            registry.OpenKey.side_effect = FileNotFoundError
            self.assertFalse(desktop.webview2_available())
            registry.OpenKey.side_effect = None
            registry.QueryValueEx.return_value = ("86.0.0.0", 1)
            self.assertFalse(desktop.webview2_available())
            registry.QueryValueEx.return_value = ("120.0.0.0", 1)
            self.assertTrue(desktop.webview2_available())


class DesktopControlTests(unittest.TestCase):
    def setUp(self):
        self.runtime = mock.Mock()
        self.runtime.closing = False
        self.runtime.authorized.side_effect = lambda value: value == "test-control"
        self.patch = mock.patch.object(webui, "runtime", self.runtime)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = webui.app.test_client()
        self.headers = {"X-FDU-App-Token": "test-control"}

    def test_external_quit_without_token_is_rejected(self):
        self.assertEqual(self.client.post("/api/app/quit", json={}).status_code, 403)
        self.runtime.request_exit.assert_not_called()

    def test_quit_running_task_needs_explicit_stop(self):
        with mock.patch.object(webui.tasks, "status", return_value={"running": True}):
            response = self.client.post("/api/app/quit", json={}, headers=self.headers)
            self.assertEqual(response.status_code, 409)
            self.runtime.request_exit.assert_not_called()
            response = self.client.post("/api/app/quit", json={"stop_task": True}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.runtime.request_exit.assert_called_once()

    def test_exit_during_preflight_remains_possible(self):
        with mock.patch.object(webui.preflight, "status", return_value={"state": "checking"}):
            response = self.client.post("/api/app/quit", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)

    def test_new_tasks_cannot_start_during_exit(self):
        self.runtime.closing = True
        self.assertEqual(self.client.post("/api/tasks", json={"action": "now"}).status_code, 409)

    def test_system_close_dispatches_ui_confirmation_without_eval(self):
        runtime = desktop.DesktopRuntime(None, "/unused", lambda: True, lambda: None, True, False)
        runtime.window = mock.Mock()
        runtime._confirm_close()
        runtime.window.run_js.assert_called_once()
        runtime.window.evaluate_js.assert_not_called()
        self.assertFalse(runtime.closing)


class WorkerLifecycleTests(unittest.TestCase):
    def test_stop_terminates_a_real_local_worker_and_preserves_unicode_output(self):
        # A harmless test entrypoint replaces the enrollment worker.
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "webui.py").write_text(
                "import time\nprint('本地测试就绪', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
            manager = webui.TaskManager()
            with mock.patch.object(webui, "RESOURCE_DIR", Path(directory)):
                manager.start("dry_run")
            try:
                deadline = time.monotonic() + 5
                while "本地测试就绪" not in manager.status(0)["output"] and time.monotonic() < deadline:
                    threading.Event().wait(.02)
                self.assertIn("本地测试就绪", manager.status(0)["output"])
                manager.stop()
                deadline = time.monotonic() + 5
                while manager.status(0)["finished_at"] is None and time.monotonic() < deadline:
                    threading.Event().wait(.02)
                result = manager.status(0)
                self.assertFalse(result["running"])
                self.assertIsNotNone(result["finished_at"])
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
