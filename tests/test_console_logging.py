import contextlib
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app
import console_log


class ConsoleLogging(unittest.TestCase):
    def setUp(self):
        self.out, self.err = io.StringIO(), io.StringIO()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(self.out))
        stack.enter_context(contextlib.redirect_stderr(self.err))
        stack.enter_context(patch.dict(os.environ, {"QUIZLENS_DEBUG": "0"}))
        stack.enter_context(patch.object(console_log, "_last_event", {}))

    def handler(self, path):
        handler = object.__new__(app.Handler)
        handler.server = SimpleNamespace()
        handler.path = path
        handler.command = "POST"
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"POST {path} HTTP/1.1"
        handler.wfile = io.BytesIO()
        handler.send = Mock(wraps=handler.send)
        return handler

    def test_aborted_socket_never_gets_a_second_response(self):
        for route_error in (None, ValueError("invalid request")):
            for successful_writes in (0, 1):  # Windows can abort on either headers or body.
                with self.subTest(route_error=route_error, successful_writes=successful_writes):
                    handler = self.handler("/api/ocr")
                    handler.route = Mock(return_value={"texts": []}, side_effect=route_error)
                    handler.wfile = Mock()
                    aborted = ConnectionAbortedError(10053, "WSAECONNABORTED")
                    handler.wfile.write.side_effect = [None] * successful_writes + [aborted]
                    handler.dispatch(True)
                    self.assertEqual(handler.send.call_count, 1)
                    self.assertTrue(handler.close_connection)
        self.assertNotIn("Traceback", self.err.getvalue())
        self.assertNotIn("10053", self.err.getvalue())
        self.assertEqual(self.err.getvalue().count("HTTP 400"), 1)

    def test_aborted_upload_and_backend_failure_are_distinct(self):
        handler = self.handler("/api/ocr")
        handler.headers = {"Content-Length": "5"}
        handler.rfile = Mock()
        handler.rfile.read.side_effect = ConnectionResetError()
        handler.route = lambda _: handler.body(10)
        handler.dispatch(True)
        handler.send.assert_not_called()
        self.assertEqual(self.err.getvalue(), "")
        handler.route = Mock(side_effect=ConnectionAbortedError(10053, "private upstream context"))
        handler.dispatch(True)
        self.assertEqual(handler.send.call_args.args[0], 500)
        self.assertIn("HTTP 500", self.err.getvalue())
        self.assertNotIn("private upstream context", self.err.getvalue())

    def test_console_records_source_events_without_upload_names_or_debug_payloads(self):
        handler = self.handler("/api/import?name=private-lecture.md")
        handler.route = Mock(return_value={"lecture": {"name": "private-lecture.md"}})
        handler.dispatch(True)
        self.assertRegex(self.out.getvalue(), r"\[\d\d:\d\d:\d\d\] \[ИНФО\] \[Документы\]")
        self.assertNotIn("private-lecture", self.out.getvalue())
        handler.route = Mock(side_effect=OSError("private-content"))
        with patch.dict(os.environ, {"QUIZLENS_DEBUG": "1"}):
            handler.dispatch(True)
            handler.dispatch(True)
        warning = self.err.getvalue()
        self.assertEqual(warning.count("HTTP 500"), 1)
        self.assertIn("OSError", warning)
        self.assertIn("dispatch", warning)
        self.assertNotIn("private", warning)

    def test_unrelated_write_error_is_reported_once_without_retry(self):
        handler = self.handler("/api/health")
        handler.route = Mock(return_value={"app": "QuizLens"})
        handler.wfile = Mock()
        handler.wfile.write.side_effect = OSError("private-error")
        handler.dispatch()
        handler.send.assert_called_once()
        self.assertTrue(handler.close_connection)
        self.assertIn("Не удалось передать ответ", self.err.getvalue())
        self.assertNotIn("private-error", self.err.getvalue())

    def test_socketserver_errors_before_dispatch_are_logged_without_request_content(self):
        server = object.__new__(app.QuizServer)
        for error in (ConnectionResetError(), OSError("private-error")):
            try:
                raise error
            except OSError:
                server.handle_error(None, ("127.0.0.1", 1234))
        self.assertEqual(self.err.getvalue().count("[ОШИБКА]"), 1)
        self.assertNotIn("private-error", self.err.getvalue())

    def test_oversized_upload_explains_file_limit(self):
        handler = self.handler("/api/import")
        handler.headers = {"Content-Length": str(app.MAX_IMPORT + 1)}
        handler.route = lambda _: handler.body(app.MAX_IMPORT, upload=True)
        handler.dispatch(True)
        self.assertEqual(handler.send.call_args.args[0], 400)
        self.assertIn("До 32 МиБ на файл", handler.wfile.getvalue().decode())
