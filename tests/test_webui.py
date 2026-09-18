import copy
import datetime as dt
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import webui


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = webui.default_config()
        self.config["courses"] = [
            {
                "name": "测试课程",
                "kcdm": "CS50014",
                "bjdm": "2026202701CS50014.01",
                "lx": "8",
                "bqmc": "6",
                "enabled": True,
                "full_max_tries": 0,
            }
        ]

    def test_valid_config_is_normalized(self):
        result = webui.validate_config(self.config)
        self.assertFalse(result["serial_mode"])
        self.assertNotIn("full_max_tries", result["courses"][0])
        self.assertEqual(result["full_max_tries"], 0)

    def test_rejects_more_than_ten_enabled_courses(self):
        config = copy.deepcopy(self.config)
        template = config["courses"][0]
        config["courses"] = [
            {**template, "name": f"测试课程 {index}", "bjdm": f"class-{index}"}
            for index in range(11)
        ]
        with self.assertRaises(webui.ConfigValidationError):
            webui.validate_config(config)

    def test_rejects_non_fdu_target(self):
        config = copy.deepcopy(self.config)
        config["target"] = "example.com"
        with self.assertRaises(webui.ConfigValidationError):
            webui.validate_config(config)

    def test_rejects_end_before_start(self):
        config = copy.deepcopy(self.config)
        config["end_time"] = config["start_time"]
        with self.assertRaises(webui.ConfigValidationError):
            webui.validate_config(config)

    def test_rejects_unsafe_request_rate(self):
        config = copy.deepcopy(self.config)
        config["request_interval"] = 0.01
        with self.assertRaises(webui.ConfigValidationError):
            webui.validate_config(config)

    def test_extracts_raw_cookie_header(self):
        cookie = webui.extract_cookie("Cookie: JSESSIONID=abc; _WEU=def; theme=light")
        self.assertEqual(cookie, "JSESSIONID=abc; _WEU=def; theme=light")

    def test_extracts_cookie_from_chrome_curl(self):
        copied = "curl 'http://yjsxk.fudan.edu.cn/' -H 'accept: text/html' -H 'cookie: _WEU=def; JSESSIONID=abc'"
        cookie = webui.extract_cookie(copied)
        self.assertEqual(cookie, "_WEU=def; JSESSIONID=abc")

    def test_rejects_incomplete_cookie(self):
        with self.assertRaises(webui.ConfigValidationError):
            webui.extract_cookie("JSESSIONID=abc")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.path_patch = mock.patch.object(webui, "CONFIG_PATH", root / "config.json")
        self.backup_patch = mock.patch.object(webui, "CONFIG_BACKUP_PATH", root / "config.json.webui.bak")
        self.cookie_patch = mock.patch.object(webui, "COOKIE_PATH", root / "cookie.txt")
        self.path_patch.start()
        self.backup_patch.start()
        self.cookie_patch.start()
        self.preflight_patch = mock.patch.object(webui, "preflight", webui.Preflight())
        self.preflight_patch.start()
        self.client = webui.app.test_client()

    def tearDown(self):
        self.path_patch.stop()
        self.backup_patch.stop()
        self.cookie_patch.stop()
        self.preflight_patch.stop()
        self.tempdir.cleanup()

    def test_get_returns_defaults_without_creating_file(self):
        response = self.client.get("/api/config")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["exists"])
        self.assertEqual(payload["config"]["target"], webui.ALLOWED_TARGET)

    def test_put_saves_and_backs_up_config(self):
        config = webui.default_config()
        first = self.client.put("/api/config", json={"config": config})
        self.assertEqual(first.status_code, 200)
        config["full_max_tries"] = 7  # 旧配置会被迁移为持续重试。
        second = self.client.put("/api/config", json={"config": config})
        self.assertEqual(second.status_code, 200)
        self.assertTrue(webui.CONFIG_BACKUP_PATH.exists())
        stored = json.loads(webui.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(stored["full_max_tries"], 0)

    def test_unknown_task_is_rejected(self):
        response = self.client.post("/api/tasks", json={"action": "shell"})
        self.assertEqual(response.status_code, 400)

    @mock.patch.object(webui.tasks, "start")
    def test_login_can_start_with_empty_default_config(self, start):
        start.return_value = {"running": True, "action": "login"}
        response = self.client.post("/api/tasks", json={"action": "login"})
        self.assertEqual(response.status_code, 200)
        start.assert_called_once_with("login")
        self.assertTrue(webui.CONFIG_PATH.exists())

    @mock.patch.object(webui, "verify_cookie")
    def test_cookie_import_validates_and_saves_file_only(self, verify):
        secret = "JSESSIONID=abc; _WEU=def"
        response = self.client.put("/api/cookie", json={"cookie": secret})
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(secret, json.dumps(payload))
        self.assertEqual(webui.COOKIE_PATH.read_text(encoding="utf-8"), secret)
        if sys.platform != "win32":
            self.assertEqual(webui.COOKIE_PATH.stat().st_mode & 0o777, 0o600)
        stored = json.loads(webui.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(stored["cookie_source"], "file")
        verify.assert_called_once()

    def test_page_refresh_does_not_clear_saved_cookie(self):
        secret = "JSESSIONID=abc; _WEU=def"
        webui.COOKIE_PATH.write_text(secret, encoding="utf-8")

        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/api/config").status_code, 200)
        self.assertEqual(webui.COOKIE_PATH.read_text(encoding="utf-8"), secret)
        payload = self.client.get("/api/config").get_json()
        self.assertTrue(payload["login"]["saved"])
        self.assertNotIn(secret, json.dumps(payload))

    @mock.patch.object(webui, "verify_cookie")
    @mock.patch.object(webui.preselect, "fetch_lists")
    def test_catalog_uses_saved_cookie_and_normalizes_courses(self, fetch_lists, verify):
        webui.COOKIE_PATH.write_text("JSESSIONID=abc; _WEU=def", encoding="utf-8")
        fetch_lists.return_value = [
            (
                "学科专业课", 8, "专业选修课", 6,
                [{
                    "KCMC": "测试课程", "KCDM": "CS50014",
                    "BJDM": "2026202701CS50014.01", "RKJS": "测试教师",
                    "RWKKDWDM": "012", "RWKKDWMC": "计算机科学技术学院",
                    "KCXF": "3", "KXRS": 30, "DQRS": 29,
                    "PKSJ": "周一 1-2", "PKDD": "H2101", "XQMC": "邯郸",
                    "IS_CONFLICT": 0,
                }],
            )
        ]
        courses = webui.load_course_catalog()
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0]["remaining"], 1)
        self.assertEqual(courses[0]["bqmc"], "6")
        self.assertEqual(courses[0]["department"], "012 计算机科学技术学院")
        self.assertFalse(courses[0]["full"])
        verify.assert_called_once()

    @mock.patch.object(webui, "load_course_catalog")
    def test_catalog_api_does_not_return_cookie(self, load_catalog):
        load_catalog.return_value = [{"name": "测试课程", "bjdm": "class-1"}]
        response = self.client.get("/api/courses")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        self.assertNotIn("cookie", json.dumps(payload).lower())

    @mock.patch.object(webui.tasks, "start")
    @mock.patch.object(webui, "validate_configured_course_scope")
    def test_task_start_blocks_course_outside_student_scope(self, validate_scope, start):
        config = webui.default_config()
        config["courses"] = [{
            "name": "不可选课程",
            "kcdm": "GEIP40013",
            "bjdm": "2026202701GEIP40013.01",
            "lx": "7",
            "bqmc": "1",
            "enabled": True,
        }]
        webui.save_config(config)
        validate_scope.side_effect = webui.ConfigValidationError("当前账号不可选：不可选课程")

        response = self.client.post("/api/tasks", json={"action": "now"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("当前账号不可选", response.get_json()["error"])
        start.assert_not_called()


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        for key, value in {"CONFIG_PATH": root / "config.json",
                           "CONFIG_BACKUP_PATH": root / "config.bak",
                           "COOKIE_PATH": root / "cookie.txt",
                           "preflight": webui.Preflight(), "tasks": webui.TaskManager()}.items():
            patch = mock.patch.object(webui, key, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.config = webui.default_config()
        self.config["courses"] = [{"name": "模拟课程", "kcdm": "TEST",
                                   "bjdm": "TEST.01", "lx": "7", "bqmc": "1", "enabled": True}]
        webui.save_config(self.config)
        webui.COOKIE_PATH.write_text("JSESSIONID=test; _WEU=test", encoding="utf-8")
        self.client = webui.app.test_client()

    def wait_for_check(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = self.client.get("/api/status").get_json()["preflight"]
            if result["state"] != "checking":
                return result
            threading.Event().wait(0.01)
        self.fail("Self-check did not finish")

    @mock.patch.object(webui, "verify_cookie")
    @mock.patch.object(webui, "validate_configured_course_scope")
    def test_check_progress_survives_refresh_and_blocks_mutations(self, scope, verify):
        entered, release = threading.Event(), threading.Event()
        verify.side_effect = lambda *_: (entered.set(), release.wait(2))
        try:
            self.assertEqual(self.client.post("/api/preflight", json={}).status_code, 202)
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.client.get("/").status_code, 200)
            status = self.client.get("/api/status").get_json()
            self.assertEqual(status["preflight"]["state"], "checking")
            for method, path, payload in [("put", "/api/config", {"config": self.config}),
                                           ("put", "/api/cookie", {"cookie": "unused"}),
                                           ("post", "/api/tasks", {"action": "now"})]:
                self.assertEqual(getattr(self.client, method)(path, json=payload).status_code, 409)
        finally:
            release.set()
        result = self.wait_for_check()
        self.assertTrue(result["valid"])
        self.assertEqual([c["key"] for c in result["checks"]], ["login", "courses"])
        scope.assert_called_once()

    @mock.patch.object(webui, "verify_cookie")
    @mock.patch.object(webui, "validate_configured_course_scope")
    def test_course_change_cookie_change_and_age_invalidate_pass(self, scope, verify):
        self.client.post("/api/preflight", json={})
        self.assertTrue(self.wait_for_check()["valid"])
        self.config["courses"][0]["bjdm"] = "TEST.02"
        webui.save_config(self.config)
        self.assertFalse(webui.preflight.status()["valid"])
        self.client.post("/api/preflight", json={})
        self.assertTrue(self.wait_for_check()["valid"])
        webui.COOKIE_PATH.unlink()
        self.assertFalse(webui.preflight.status()["valid"])
        webui.COOKIE_PATH.write_text("JSESSIONID=new; _WEU=new", encoding="utf-8")
        self.client.post("/api/preflight", json={})
        self.assertTrue(self.wait_for_check()["valid"])
        webui.preflight.checked_at -= 301
        self.assertFalse(webui.preflight.status()["valid"])

    @mock.patch.object(webui, "verify_cookie", side_effect=webui.ConfigValidationError("登录已失效"))
    @mock.patch.object(webui, "validate_configured_course_scope")
    def test_failed_login_blocks_run_and_new_cookie_clears_failure(self, scope, verify):
        self.client.post("/api/preflight", json={})
        result = self.wait_for_check()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["checks"][0]["message"], "登录已失效")
        with mock.patch.object(webui.tasks, "start") as start:
            self.assertEqual(self.client.post("/api/tasks", json={"action": "now", "require_preflight": True}).status_code, 409)
            start.assert_not_called()
        scope.assert_not_called()
        webui.COOKIE_PATH.unlink()
        self.assertEqual(webui.preflight.status()["state"], "idle")
        self.assertEqual(webui.preflight.status()["checks"], [])

    @mock.patch.object(webui, "verify_cookie")
    @mock.patch.object(webui, "validate_configured_course_scope", side_effect=webui.ConfigValidationError("当前账号不可选"))
    def test_scope_failure_is_reported_after_login_pass(self, scope, verify):
        self.client.post("/api/preflight", json={})
        result = self.wait_for_check()
        self.assertEqual(result["state"], "failed")
        self.assertTrue(result["checks"][0]["ok"])
        self.assertEqual(result["checks"][1]["key"], "courses")
        self.assertFalse(result["checks"][1]["ok"])

    @mock.patch.object(webui, "verify_cookie")
    @mock.patch.object(webui, "validate_configured_course_scope")
    def test_guarded_start_rechecks_scope_and_rejects_expired_deadline(self, scope, verify):
        self.client.post("/api/preflight", json={})
        self.assertTrue(self.wait_for_check()["valid"])
        with mock.patch.object(webui.tasks, "start", return_value={"running": True}) as start:
            response = self.client.post("/api/tasks", json={"action": "now", "require_preflight": True})
            self.assertEqual(response.status_code, 200)
            start.assert_called_once_with("now")
        self.assertEqual(scope.call_count, 2)
        now = dt.datetime.now()
        self.config["start_time"] = (now - dt.timedelta(hours=2)).strftime(webui.DATETIME_FORMAT)
        self.config["end_time"] = (now - dt.timedelta(hours=1)).strftime(webui.DATETIME_FORMAT)
        webui.save_config(self.config)
        with mock.patch.object(webui.tasks, "start") as start:
            response = self.client.post("/api/tasks", json={"action": "now", "require_preflight": True})
            self.assertEqual(response.status_code, 400)
            self.assertIn("截止时间", response.get_json()["error"])
            start.assert_not_called()

    def test_task_log_reports_trimmed_cursor(self):
        webui.tasks.output = "remaining"
        webui.tasks.output_base = 200
        result = webui.tasks.status(5)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["output"], "remaining")
        self.assertEqual(result["cursor"], 209)

    @mock.patch.object(webui.threading, "Thread")
    @mock.patch.object(webui.subprocess, "Popen")
    def test_source_worker_uses_absolute_entrypoint(self, popen, thread):
        popen.return_value.poll.return_value = None
        webui.tasks.start("dry_run")
        self.assertEqual(popen.call_args.args[0][2], str(webui.RESOURCE_DIR / "webui.py"))
        self.assertEqual(popen.call_args.args[0][-2:], ["--worker-action", "dry_run"])


if __name__ == "__main__":
    unittest.main()
