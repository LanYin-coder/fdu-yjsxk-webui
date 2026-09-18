import subprocess
import sys
import threading
import types
import unittest
from unittest import mock

from src import grab


class FakeGrabber:
    cfg = {"full_max_tries": 3}

    def submit(self, course):
        return False, "课程已满"


class RejectedGrabber:
    cfg = {"full_max_tries": 0}

    def submit(self, course):
        return False, "选择的教学班不在您的可选范围内 (#bjzc5)"


class BurstGrabber:
    cfg = {"full_max_tries": 1}

    def __init__(self, count):
        self.barrier = threading.Barrier(count)
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.submissions_finished = 0
        self.polls_started_after = []

    def submit(self, course):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.barrier.wait(timeout=1)
        with self.lock:
            self.active -= 1
            self.submissions_finished += 1
        return True, course["bjdm"]

    def poll_result(self, _xid):
        self.polls_started_after.append(self.submissions_finished)
        return 0, "课程已满"


class CourseRetryTests(unittest.TestCase):
    @mock.patch.object(grab.time, "sleep", return_value=None)
    def test_legacy_threshold_does_not_stop_full_course_retries(self, _sleep):
        course = {
            "name": "重点课程",
            "bjdm": "2026202701CS50014.01",
            "full_max_tries": 1,
        }
        pending = [course]
        dropped = []
        grab.run_serial(FakeGrabber(), pending, [], dropped, 0)
        self.assertEqual(pending, [course])
        self.assertEqual(dropped, [])

    @mock.patch.object(grab.time, "sleep", return_value=None)
    def test_permanent_eligibility_rejection_is_not_retried(self, _sleep):
        course = {"name": "资格不符课程", "bjdm": "class-1"}
        pending = [course]
        dropped = []
        grab.run_serial(RejectedGrabber(), pending, [], dropped, 0)
        self.assertEqual(pending, [])
        self.assertEqual(dropped, ["资格不符课程"])


class BurstSubmissionTests(unittest.TestCase):
    def test_ten_submission_starts_are_scheduled_inside_one_second(self):
        delays = grab.submission_delays(10)
        self.assertEqual(len(delays), 10)
        self.assertEqual(delays[0], 0)
        self.assertLess(delays[-1], 1.0)

    def test_all_submissions_start_before_result_polling(self):
        courses = [
            {"name": f"课程 {index}", "bjdm": f"class-{index}"}
            for index in range(10)
        ]
        pending = list(courses)
        dropped = []
        fake = BurstGrabber(len(courses))

        grab.run_burst(fake, pending, [], dropped, window=0.01)

        self.assertEqual(fake.max_active, 10)
        self.assertEqual(fake.polls_started_after, [10] * 10)
        self.assertEqual(pending, courses)
        self.assertEqual(dropped, [])

    def test_isolated_submit_keeps_cookie_and_csrf(self):
        cfg = {"target": grab.DOMAIN, "http_timeout": 12}
        client = grab.Grabber(cfg, "JSESSIONID=session; _WEU=user")
        client.token = "a" * 32
        response = mock.Mock()
        response.json.return_value = {"code": 1, "msg": "xid-1"}
        worker = mock.Mock()
        worker.post.return_value = response
        context = mock.MagicMock()
        context.__enter__.return_value = worker

        with mock.patch.object(grab.requests, "Session", return_value=context):
            accepted, xid = client.submit_isolated({"bjdm": "class-1", "lx": 7, "bqmc": 1})

        self.assertTrue(accepted)
        self.assertEqual(xid, "xid-1")
        call = worker.post.call_args
        self.assertEqual(call.kwargs["headers"]["Cookie"], "JSESSIONID=session; _WEU=user")
        self.assertEqual(call.kwargs["data"]["csrfToken"], "a" * 32)


class CookieReadTests(unittest.TestCase):
    def test_macos_keychain_timeout_becomes_readable_error(self):
        original_reader = lambda service, user: b"secret"
        fake_module = types.SimpleNamespace(_get_osx_keychain_password=original_reader)

        def chrome(domain_name=""):
            fake_module._get_osx_keychain_password("Chrome Safe Storage", "Chrome")
            return []

        fake_module.chrome = chrome
        fake_module.edge = chrome
        timeout = subprocess.TimeoutExpired(["security"], grab.KEYCHAIN_TIMEOUT)
        with mock.patch.dict(sys.modules, {"browser_cookie3": fake_module}), \
                mock.patch.object(grab.sys, "platform", "darwin"), \
                mock.patch.object(grab.subprocess, "run", side_effect=timeout):
            value, error = grab.cookie_from_browser("chrome")

        self.assertIsNone(value)
        self.assertIn("钥匙串授权等待超过", error)
        self.assertIs(fake_module._get_osx_keychain_password, original_reader)


if __name__ == "__main__":
    unittest.main()
