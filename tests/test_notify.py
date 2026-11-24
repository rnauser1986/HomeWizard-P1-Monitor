import json
import unittest
from unittest import mock

from tests.helpers import fresh

URL = "http://signal.test:8080"
SENDER = "+155501"
RECIPIENT = "+155502"


class SendSignalTests(unittest.TestCase):
    def setUp(self):
        self.mod = fresh("notify_signal")
        patchers = [
            mock.patch.object(self.mod, "SIGNAL_URL", URL),
            mock.patch.object(self.mod, "SIGNAL_FROM", SENDER),
            mock.patch.object(self.mod, "SIGNAL_TO", RECIPIENT),
            mock.patch.object(self.mod, "COOLDOWN_SEC", 30.0),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.urlopen = mock.patch("urllib.request.urlopen").start()
        self.addCleanup(mock.patch.stopall)
        self.printed = mock.patch("builtins.print").start()

    def logged(self):
        return " ".join(str(c.args[0]) for c in self.printed.call_args_list)

    def test_message_is_posted_to_the_signal_server(self):
        self.mod.send_signal("hello", now=100.0)
        req = self.urlopen.call_args.args[0]
        self.assertEqual(req.full_url, URL + "/v2/send")
        self.assertEqual(json.loads(req.data), {"message": "hello", "number": SENDER, "recipients": [RECIPIENT]})
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], self.mod.TIMEOUT_SEC)

    def test_message_is_always_logged(self):
        self.mod.send_signal("hello", now=100.0)
        self.assertIn("[SIGNAL_MSG] hello", self.logged())

    def test_nothing_is_sent_when_signal_is_not_set_up(self):
        for name in ("SIGNAL_URL", "SIGNAL_FROM", "SIGNAL_TO"):
            with mock.patch.object(self.mod, name, ""):
                self.mod.send_signal("hello", now=100.0)
        self.urlopen.assert_not_called()
        self.assertEqual(self.logged().count("[SIGNAL_MSG] hello"), 3)

    def test_same_text_is_not_sent_twice_inside_the_cooldown(self):
        self.mod.send_signal("same", now=100.0)
        self.mod.send_signal("same", now=129.9)
        self.assertEqual(self.urlopen.call_count, 1)
        self.mod.send_signal("same", now=130.0)
        self.assertEqual(self.urlopen.call_count, 2)

    def test_other_text_is_sent_at_once(self):
        self.mod.send_signal("one", now=100.0)
        self.mod.send_signal("two", now=100.0)
        self.assertEqual(self.urlopen.call_count, 2)

    def test_old_messages_are_forgotten(self):
        for i in range(500):
            self.mod.send_signal(f"message {i}", now=100.0 + i)
        self.assertLessEqual(len(self.mod._last_sent_at), 31)

    def test_a_failing_server_never_raises_and_is_logged(self):
        self.urlopen.side_effect = OSError("connection refused")
        self.mod.send_signal("hello", now=100.0)
        self.assertIn("[SIGNAL_ERR] connection refused", self.logged())

    def test_numbers_and_address_never_reach_the_log(self):
        self.urlopen.side_effect = OSError("connection refused")
        self.mod.send_signal("hello", now=100.0)
        for secret in (URL, SENDER, RECIPIENT):
            self.assertNotIn(secret, self.logged())


if __name__ == "__main__":
    unittest.main()
