import unittest

from src import preselect


class CourseScopeTests(unittest.TestCase):
    def setUp(self):
        self.student = {
            "NJDM": "2026",
            "XSLBDM": "01",
            "YXDM": "125",
            "ZYDM": "100211",
            "PYCCDM": "1",
            "YJBYSJ_INT": 20300630,
        }
        self.now = 20260905194700

    def test_rejects_course_for_other_grades_and_student_types(self):
        rules = [{
            "KXNJDM": "2025,2024,2023,2022",
            "KXXSLBDM": "00,11,13",
            "KSSJTIMES": 20260830100000,
            "JSSJTIMES": 20260921100000,
        }]
        self.assertFalse(preselect.course_in_student_scope(rules, self.student, self.now, True))

    def test_accepts_matching_allow_rule(self):
        rules = [{
            "KXNJDM": "2026",
            "KXXSLBDM": "01,11",
            "KSSJTIMES": 20260830100000,
            "JSSJTIMES": 20260921100000,
        }]
        self.assertTrue(preselect.course_in_student_scope(rules, self.student, self.now, False))

    def test_matching_exclusion_rule_rejects_course(self):
        rules = [{"KXNJDM": "2026", "JXPYCCDM": "1"}]
        self.assertFalse(preselect.course_in_student_scope(rules, self.student, self.now, False))

    def test_expired_restriction_does_not_hide_course(self):
        rules = [{
            "KXNJDM": "2025",
            "KSSJTIMES": 20260801000000,
            "JSSJTIMES": 20260831235959,
        }]
        self.assertTrue(preselect.course_in_student_scope(rules, self.student, self.now, False))

    def test_empty_scope_obeys_server_setting(self):
        self.assertTrue(preselect.course_in_student_scope(None, self.student, self.now, True))
        self.assertFalse(preselect.course_in_student_scope(None, self.student, self.now, False))


if __name__ == "__main__":
    unittest.main()
