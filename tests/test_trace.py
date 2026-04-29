import tempfile
import unittest
from pathlib import Path

from drt import DRTRuntime, DRTThread, runtime_yield
from drt.events import (
    EventType,
    LogEntry,
    serialize_io_read_payload,
)
from drt.log import EventLog
from drt.trace import (
    format_explain,
    format_timeline,
    load_trace,
    write_html_report,
)


class TestTraceInspection(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.tempdir.name) / "execution.log"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_timeline_and_explain_summarize_runtime_log(self):
        def program():
            def worker():
                runtime_yield()

            thread = DRTThread(target=worker)
            thread.start()
            thread.join()

        runtime = DRTRuntime(mode="record", log_path=str(self.log_path))
        runtime.run(program)

        timeline = format_timeline(self.log_path)
        explain = format_explain(self.tempdir.name)
        summary = load_trace(self.log_path)

        self.assertGreater(summary.event_count, 0)
        self.assertIn("Integrity: verified", timeline)
        self.assertIn("THREAD_CREATE", timeline)
        self.assertIn("THREAD_EXIT", explain)
        self.assertIn("thread 0", explain)
        self.assertIn("thread 1", timeline)

    def test_html_report_escapes_decoded_payload_content(self):
        log = EventLog(self.log_path)
        log.open_for_record()
        log.append(
            LogEntry(
                logical_time=0,
                thread_id=7,
                event_type=EventType.IO_READ,
                payload=serialize_io_read_payload("<unsafe & path>", 12, b"abc"),
            )
        )
        log.finalize()
        log.close()

        output_path = Path(self.tempdir.name) / "trace.html"
        written = write_html_report(self.log_path, output_path)
        html = written.read_text(encoding="utf-8")

        self.assertEqual(written, output_path)
        self.assertIn("IO_READ", html)
        self.assertIn("&lt;unsafe &amp; path&gt;", html)
        self.assertNotIn("<unsafe & path>", html)


if __name__ == "__main__":
    unittest.main()
