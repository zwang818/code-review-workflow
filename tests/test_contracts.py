import json
import unittest

from workflow_agent.contracts import (NodeContext, code_text, parse_json, validate_generation,
                                      validate_repair, validate_review)
from workflow_agent.errors import ContractError
from workflow_agent.validators import validate_syntax


class ContractTests(unittest.TestCase):
    def test_json_must_be_complete_unique_and_finite(self):
        for raw in ('```json\n{}\n```', '[]', '{"x":1,"x":2}', '{"x":NaN}', 'invalid'):
            with self.subTest(raw=raw), self.assertRaises(ContractError):
                parse_json(raw)

    def test_generation_requires_exact_fields_and_bounded_source(self):
        valid = {"code": "x = 1\n", "summary": "说明", "assumptions": []}
        self.assertEqual(validate_generation(valid), valid)
        for data in (dict(valid, path="../outside.py"), dict(valid, code=""),
                     dict(valid, code="x" * 20001), dict(valid, assumptions="none")):
            with self.subTest(data=list(data)), self.assertRaises(ContractError):
                validate_generation(data)

    def test_canonical_source_preserves_utf8_consistency(self):
        source = code_text("# coding: latin-1\r\nvalue = 'café'\r\n")
        self.assertEqual(source, "# coding: utf-8\nvalue = 'café'\n")

    def test_review_binds_evidence_range_and_version(self):
        ctx = NodeContext(requirement="test", source="x = 1\ny = 2\n", code_version=2)
        issue = {"line_start": 1, "line_end": 1, "severity": "low", "title": "test",
                 "evidence": "x = 1", "trigger": "test", "impact": "test", "suggestion": "test"}
        base = {"code_version": 2, "coverage": "complete", "scope_note": "test",
                "issues": [issue], "limitations": []}
        validate_review(base, ctx)
        cases = [dict(base, code_version=1), dict(base, code_version=True),
                 dict(base, issues=[dict(issue, evidence="y = 2")]),
                 dict(base, issues=[dict(issue, line_start=3, line_end=3)]),
                 dict(base, issues=[dict(issue, severity="critical")])]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ContractError):
                validate_review(data, ctx)

    def test_repair_must_cover_known_ids_exactly_once(self):
        ctx = NodeContext(requirement="test", issues=[{"id": "R001-I001"}])
        change = {"issue_id": "R001-I001", "action": "changed", "explanation": "changed"}
        valid = {"code": "x=1", "changes": [change], "remaining_notes": []}
        validate_repair(valid, ctx)
        for changes in ([], [change, change], [dict(change, issue_id="unknown")], [dict(change, action="verified")]):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                validate_repair(dict(valid, changes=changes), ctx)

    def test_compilation_catches_contextual_syntax_without_execution(self):
        self.assertEqual(validate_syntax("return 1", 1)["syntax"], "failed")
        self.assertEqual(validate_syntax("raise RuntimeError('do not execute')", 1)["syntax"], "passed")
        self.assertEqual(validate_syntax("x = 1", 2)["runtime_tests"], "not_run")


if __name__ == "__main__":
    unittest.main()
