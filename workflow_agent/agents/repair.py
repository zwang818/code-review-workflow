from .base import WorkflowNode
from ..contracts import NodeResult, validate_repair


class RepairAgent(WorkflowNode):
    name = "repair"

    def run(self, context) -> NodeResult:
        messages = self.messages(context.payload())
        message = self.client.complete(messages)
        data = self.parse_with_correction(messages, message, lambda d: validate_repair(d, context))
        return NodeResult(self.name, data)
