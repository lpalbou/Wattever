"""Cancellation ownership and endpoint identity without engines, sockets or CUDA."""
import threading
import unittest
from unittest.mock import Mock, patch

from wattever import capabilities, endpoints, runner, server


def bare_runner(cls):
    value = object.__new__(cls)
    value.run = {"id": "owned-run"}
    value._start_lock = threading.Lock()
    value.cancel_event = threading.Event()
    value._responses = set()
    value._responses_lock = threading.Lock()
    value._response_lock = threading.Lock()
    return value


class OwnedCancellationTests(unittest.TestCase):
    classes = (runner.BenchRunner, capabilities.CapabilityRunner)

    def test_foreign_ids_and_malformed_ids_never_cancel(self):
        for cls in self.classes:
            with self.subTest(cls=cls.__name__):
                value = bare_runner(cls)
                response = Mock()
                value._responses.add(response)
                self.assertFalse(value.cancel_if_current("somebody-else"))
                self.assertFalse(value.cancel_event.is_set())
                response.close.assert_not_called()
                for invalid in (None, "", " \t", 42, False, [], {}):
                    with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                        value.cancel_if_current(invalid)
                    self.assertFalse(value.cancel_event.is_set())

    def test_check_and_signal_hold_the_same_start_mutex(self):
        for cls in self.classes:
            with self.subTest(cls=cls.__name__):
                value = bare_runner(cls)
                actual_signal = value._cancel_current
                observed = []

                def signal():
                    unlocked = value._start_lock.acquire(blocking=False)
                    if unlocked:
                        value._start_lock.release()
                    self.assertFalse(unlocked, "Ownership check and signal must share the start mutex")
                    observed.append(value.run["id"])
                    actual_signal()

                value._cancel_current = signal
                self.assertTrue(value.cancel_if_current("owned-run"))
                self.assertEqual(observed, ["owned-run"])
                self.assertTrue(value.cancel_event.is_set())

    def test_cancel_waits_for_a_replacement_transition_then_rejects_old_id(self):
        for cls in self.classes:
            with self.subTest(cls=cls.__name__):
                value = bare_runner(cls)
                entered, finished = threading.Event(), threading.Event()
                result = []

                def cancel_old():
                    entered.set()
                    result.append(value.cancel_if_current("owned-run"))
                    finished.set()

                with value._start_lock:
                    worker = threading.Thread(target=cancel_old)
                    worker.start()
                    self.assertTrue(entered.wait(1))
                    self.assertFalse(finished.is_set())
                    value.run = {"id": "new-run"}
                    value.cancel_event.clear()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result, [False])
                self.assertFalse(value.cancel_event.is_set())

    def test_matching_id_closes_only_owned_response_and_legacy_cancel_works(self):
        for cls in self.classes:
            with self.subTest(cls=cls.__name__):
                value = bare_runner(cls)
                closed = threading.Event()
                value._responses.add(Mock(close=closed.set))
                self.assertTrue(value.cancel_if_current("owned-run"))
                self.assertTrue(closed.wait(1))
                self.assertTrue(value.cancel_event.is_set())
                value.cancel_event.clear()
                value._responses.clear()
                value.cancel()
                self.assertTrue(value.cancel_event.is_set())


class CancellationRouteTests(unittest.TestCase):
    def handler(self, path, body):
        value = object.__new__(server.Handler)
        value.path = path
        value._mutation_guard = lambda: None
        value._body = lambda: body
        value._json = lambda payload, code=200: (code, payload)
        value._error = lambda code, message: (code, message)
        return value

    def test_both_routes_enforce_owned_id_and_keep_empty_body_compatibility(self):
        for path, cls in (("/api/bench/cancel", runner.BenchRunner),
                          ("/api/capabilities/cancel", capabilities.CapabilityRunner)):
            for body, status in (({"expected_run_id": "foreign"}, 409),
                                 ({"expected_run_id": None}, 400),
                                 ({"expected_run_id": []}, 400),
                                 ({"expected_run_id": ""}, 400),
                                 ({"expected_run_id": "owned-run"}, 200), ({}, 200)):
                with self.subTest(path=path, body=body):
                    value = bare_runner(cls)
                    with patch.object(server, "BENCH", value), patch.object(server, "_cap_bench", return_value=value):
                        response = self.handler(path, body).do_POST()
                    self.assertEqual(response[0], status)
                    self.assertEqual(value.cancel_event.is_set(), status == 200)

    def test_version_advertises_atomic_contract(self):
        value = self.handler("/api/version", {})
        response = value.do_GET()
        self.assertTrue(response[1]["features"]["atomic_expected_run_id_cancel"])
        self.assertIs(response[1]["features"]["capability_start_transfer_probes"], False)
        self.assertEqual(response[1]["features"]["hardware_probe_policy"], ["auto", "defer"])

    def test_maya_identity_uses_explicit_engine_evidence(self):
        detected = endpoints._safe_service_evidence({"engine": "Maya", "engine_version": "1.1.52"})
        self.assertEqual(detected, {"service": "Maya", "version": "1.1.52"})
        self.assertEqual(endpoints.safe_identity_metadata(dict(detected, source="detected"))["service"], "Maya")


if __name__ == "__main__":
    unittest.main()
