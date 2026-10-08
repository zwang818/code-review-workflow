"""Serializable workflow state, owned only by the runner."""

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class WorkflowState:
    run_id: str
    mode: str
    requirement: str
    max_fix_attempts: int
    demo: bool = False
    schema_version: int = 1
    status: str = "pending"
    outcome: Optional[str] = None
    current_stage: str = "input"
    code_version: int = 0
    code_path: Optional[str] = None
    code_sha256: Optional[str] = None
    review_round: int = 0
    fix_attempt: int = 0
    model_calls: int = 0
    http_retries: int = 0
    tool_calls: int = 0
    validation: dict = field(default_factory=dict)
    latest_review_path: Optional[str] = None
    issues: list = field(default_factory=list)
    stages: list = field(default_factory=list)
    reason: Optional[str] = None
    error: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)
