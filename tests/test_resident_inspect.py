import json
from pathlib import Path
import tempfile
import unittest
from evals.resident_inspect import render


class InspectorTests(unittest.TestCase):
    def test_live_partial_transcripts_and_untrusted_content_are_readable_and_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "runs/case/collaboration"
            run.mkdir(parents=True)
            (run/"outcome.json").write_text(json.dumps({"status":"running"}))
            runtime = root / "adapter/key/runtime"
            runtime.mkdir(parents=True)
            payload = [{"path":"session", "content": json.dumps({"message":{"role":"assistant","content":[
                {"type":"text","text":"</pre><script>untrusted()</script>"}]}}) + '\n{"unfinished":'}]
            original = json.dumps(payload)
            (runtime/"transcripts.json").write_text(original)
            render(root)
            self.assertIn("outcome.json",(root/"index.html").read_text())
            html = (runtime/"conversation.html").read_text()
            self.assertNotIn("<script>",html)
            self.assertIn("&lt;script&gt;",html)
            self.assertEqual((runtime/"transcripts.json").read_text(),original)


if __name__ == "__main__":
    unittest.main()
