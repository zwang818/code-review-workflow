import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow_agent.tools import MAX_FILE_BYTES, ToolRegistry


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = ToolRegistry(self.root)

    def file(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def call(self, name, **args):
        return self.tools.execute(name, args)

    def test_numbered_read_and_pagination(self):
        self.file("a.py", "x = 1\ny = 2\nz = 3\n")
        data = self.call("read_file", path="a.py", start_line=2, end_line=2)["data"]
        self.assertEqual(data["content"], "   2 | y = 2")
        self.assertEqual(data["next_line"], 3)
        self.assertTrue(data["truncated"])

    def test_empty_file(self):
        self.file("empty.py", "")
        data = self.call("read_file", path="empty.py")["data"]
        self.assertEqual(data["total_lines"], 0)
        self.assertEqual(data["content"], "")
        self.assertIsNone(data["next_line"])
        self.assertTrue(self.call("analyze_python", path="empty.py")["data"]["syntax_ok"])

    def test_read_limit_does_not_skip_lines(self):
        self.file("a.py", "x = 1\n" * 350)
        first = self.call("read_file", path="a.py")["data"]
        second = self.call("read_file", path="a.py", start_line=first["next_line"])["data"]
        self.assertEqual(first["end_line"], 300)
        self.assertEqual(second["start_line"], 301)
        self.assertEqual(second["end_line"], 350)
        self.assertFalse(second["truncated"])

    def test_long_line_disclosure(self):
        self.file("a.py", "#" + "x" * 4000)
        data = self.call("read_file", path="a.py")["data"]
        self.assertTrue(data["truncated"])
        self.assertEqual(data["clipped_lines"], [1])

    def test_invalid_arguments_are_recoverable(self):
        self.file("a.py", "x = 1")
        cases = [("unknown", {}), ("read_file", "{invalid"), ("read_file", []),
                 ("read_file", {}), ("read_file", {"path": 3}),
                 ("read_file", {"path": "a.py", "start_line": True}),
                 ("read_file", {"path": "a.py", "start_line": -1}),
                 ("read_file", {"path": "a.py", "end_line": "2"}),
                 ("read_file", {"path": "a.py", "surprise": 1}),
                 ("read_file", {"path": "a.py", "start_line": 5}),
                 ("read_file", {"path": "a.py", "start_line": 2, "end_line": 1})]
        for name, args in cases:
            with self.subTest(args=args):
                self.assertFalse(self.tools.execute(name, args)["ok"])

    def test_path_traversal_and_hidden_file(self):
        self.file(".secret.py", "password = 'private'")
        self.file(".env", "key=private")
        for path in ("../outside.py", "/etc/passwd", ".secret.py", ".env", "missing.py", "\x00"):
            with self.subTest(path=path):
                self.assertFalse(self.call("read_file", path=path)["ok"])

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "outside.py"
            target.write_text("secret = 1")
            (self.root / "link.py").symlink_to(target)
            self.assertFalse(self.call("read_file", path="link.py")["ok"])
            self.assertEqual(self.call("list_files", path=".")["data"]["files"], [])

    def test_symlink_loop_fails_cleanly(self):
        (self.root / "loop.py").symlink_to("loop.py")
        result = self.call("read_file", path="loop.py")
        self.assertFalse(result["ok"])
        self.assertIn("符号链接", result["error"])

    def test_file_size_and_extension(self):
        self.file("huge.py", "#" * (MAX_FILE_BYTES + 1))
        self.file("a.txt", "text")
        self.assertFalse(self.call("read_file", path="huge.py")["ok"])
        self.assertFalse(self.call("read_file", path="a.txt")["ok"])

    def test_list_exclusion_and_limit(self):
        for name in ("a.py", "sub/b.py", ".hidden/c.py", "venv/d.py", "node_modules/e.py"):
            self.file(name, "x = 1")
        files = self.call("list_files", path=".")["data"]["files"]
        self.assertEqual(files, ["a.py", "sub/b.py"])
        with patch("workflow_agent.tools.MAX_FILES", 1):
            data = self.call("list_files", path=".")["data"]
            self.assertEqual(len(data["files"]), 1)
            self.assertTrue(data["truncated"])

    def test_encoding_declaration_and_bad_encoding(self):
        (self.root / "latin.py").write_bytes(b"# coding: latin-1\nname = 'caf\xe9'\n")
        self.assertIn("café", self.call("read_file", path="latin.py")["data"]["content"])
        (self.root / "bad.py").write_bytes(b"# coding: unknown-encoding\nx=1")
        self.assertFalse(self.call("read_file", path="bad.py")["ok"])

    def test_syntax_error_has_real_line(self):
        self.file("broken.py", "x = 1\ndef broken(:\n")
        data = self.call("analyze_python", path="broken.py")["data"]
        self.assertFalse(data["syntax_ok"])
        self.assertEqual(data["syntax_error"]["line"], 2)

    def test_static_analysis_never_executes_source(self):
        marker = self.root / "executed"
        self.file("danger.py", "from pathlib import Path\nPath(%r).touch()\nraise RuntimeError()" % str(marker))
        self.assertTrue(self.call("analyze_python", path="danger.py")["ok"])
        self.assertFalse(marker.exists())

    def test_findings_and_clean_control(self):
        self.file("bad.py", "def f(items=[]):\n    try:\n        return 1 / 0\n    except:\n        pass\n")
        self.file("good.py", "def f(items=None):\n    return [] if items is None else list(items)\n")
        data = self.call("analyze_python", path="bad.py")["data"]
        self.assertEqual({f["code"] for f in data["findings"]},
                         {"MUTABLE_DEFAULT", "BARE_EXCEPT", "SILENT_EXCEPTION", "ZERO_DIVISION"})
        self.assertEqual(data["findings"][0]["severity"], "高")
        self.assertEqual(self.call("analyze_python", path="good.py")["data"]["findings"], [])

    def test_dynamic_execution_is_conditional(self):
        self.file("a.py", "def evaluate(text):\n    return eval(text)\n")
        finding = self.call("analyze_python", path="a.py")["data"]["findings"][0]
        self.assertEqual(finding["line"], 2)
        self.assertIn("不可信", finding["trigger"])


if __name__ == "__main__":
    unittest.main()
