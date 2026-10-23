import unittest

from auto_parse import AutoParse


class Scheduler:
    def __init__(self):
        self.jobs = {}
        self.sequence = 0

    def schedule(self, delay, callback):
        self.sequence += 1
        self.jobs[self.sequence] = (delay, callback)
        return self.sequence

    def cancel(self, identity):
        del self.jobs[identity]

    def run_next(self):
        identity = next(iter(self.jobs))
        delay, callback = self.jobs.pop(identity)
        callback()
        return delay


class AutoParseTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = Scheduler()
        self.inputs = []
        self.latest = "first"

        def parse():
            if self.auto.begin():
                self.inputs.append(self.latest)

        self.auto = AutoParse(self.scheduler.schedule, self.scheduler.cancel, parse)

    def test_paste_schedules_automatic_parse(self):
        self.auto.request()
        self.assertEqual(320, self.scheduler.run_next())
        self.assertEqual(["first"], self.inputs)

    def test_repeated_typing_debounces_to_latest(self):
        for value in ("first", "second", "third"):
            self.latest = value
            self.auto.request()
        self.assertEqual(1, len(self.scheduler.jobs))
        self.scheduler.run_next()
        self.assertEqual(["third"], self.inputs)

    def test_new_input_during_worker_is_parsed_after_completion(self):
        self.auto.request()
        self.scheduler.run_next()
        self.latest = "second"
        self.auto.request()
        self.scheduler.run_next()
        self.assertEqual(["first"], self.inputs)
        self.auto.complete()
        self.assertEqual(0, self.scheduler.run_next())
        self.assertEqual(["first", "second"], self.inputs)

    def test_worker_completion_preserves_typing_debounce(self):
        self.auto.request()
        self.scheduler.run_next()
        self.latest = "second"
        self.auto.request()
        self.auto.complete()
        self.assertEqual(1, len(self.scheduler.jobs))
        self.assertEqual(320, self.scheduler.run_next())
        self.assertEqual(["first", "second"], self.inputs)

    def test_clear_cancels_parse(self):
        self.auto.request()
        self.auto.request(False)
        self.assertFalse(self.scheduler.jobs)
        self.assertFalse(self.auto.pending)

    def test_clear_during_worker_does_not_schedule_empty_input(self):
        self.auto.request()
        self.scheduler.run_next()
        self.auto.request()
        self.auto.request(False)
        self.auto.complete()
        self.assertFalse(self.scheduler.jobs)
        self.assertEqual(["first"], self.inputs)

    def test_manual_parse_cancels_automatic_timer(self):
        self.auto.request()
        self.assertTrue(self.auto.begin())
        self.assertFalse(self.scheduler.jobs)
        self.auto.complete()
        self.assertFalse(self.scheduler.jobs)

    def test_close_cancels_scheduled_parse(self):
        self.auto.request()
        self.auto.close()
        self.assertFalse(self.scheduler.jobs)
        self.assertFalse(self.auto.pending)
