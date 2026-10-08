"""Versioned artifacts and atomic JSON checkpoints in a unique run directory."""

import json
import os
import tempfile
import uuid
from pathlib import Path

from .errors import AgentError


class ArtifactStore:
    def __init__(self, output_root: Path):
        output_root = output_root.expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        self.run_id = "run_" + uuid.uuid4().hex
        self.root = output_root / self.run_id
        self.root.mkdir()

    def path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise AgentError("产物路径超出运行目录。") from exc
        return path

    def write_text(self, relative: str, content: str, replace: bool = False) -> str:
        target = self.path(relative)
        if target.exists() and not replace:
            raise AgentError("产物已存在，拒绝覆盖：" + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return relative

    def write_json(self, relative: str, value, replace: bool = False) -> str:
        return self.write_text(relative, json.dumps(value, ensure_ascii=False, indent=2,
                                                   allow_nan=False) + "\n", replace)
