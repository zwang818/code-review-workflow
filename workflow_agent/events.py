"""Append-only event stream; no authentication data or full prompts in logs."""

import json
from datetime import datetime, timezone


class EventLogger:
    def __init__(self, store, state, callback=None):
        self.store, self.state = store, state
        self.callback = callback or (lambda message: None)

    def emit(self, event: str, **details) -> None:
        record = {"run_id": self.state.run_id, "time": datetime.now(timezone.utc).isoformat(),
                  "stage": self.state.current_stage, "event": event,
                  "code_version": self.state.code_version, **details}
        with self.store.path("events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.callback("[%s] %s (v%d)" % (self.state.current_stage, event, self.state.code_version))
