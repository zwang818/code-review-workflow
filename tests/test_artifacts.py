import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow_agent.artifacts import ArtifactStore
from workflow_agent.errors import AgentError


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(Path(self.temp.name))

    def test_root_confinement_and_no_overwrite(self):
        with self.assertRaises(AgentError):
            self.store.write_text("../outside.py", "data")
        self.store.write_text("code/v001.py", "first")
        with self.assertRaises(AgentError):
            self.store.write_text("code/v001.py", "second")
        self.assertEqual((self.store.root / "code/v001.py").read_text(), "first")

    def test_failed_atomic_replace_leaves_old_checkpoint_intact(self):
        self.store.write_json("state.json", {"version": 1})
        with patch("workflow_agent.artifacts.os.replace", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            self.store.write_json("state.json", {"version": 2}, replace=True)
        self.assertEqual(json.loads((self.store.root / "state.json").read_text()), {"version": 1})
        self.assertEqual([p.name for p in self.store.root.iterdir()], ["state.json"])

    def test_symlink_cannot_redirect_output(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.store.root / "alias").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(AgentError):
                self.store.write_text("alias/out.py", "data")
            self.assertFalse((Path(outside) / "out.py").exists())


if __name__ == "__main__":
    unittest.main()
