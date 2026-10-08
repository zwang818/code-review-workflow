from .base import WorkflowNode
from ..contracts import NodeResult, validate_generation


class GenerateAgent(WorkflowNode):
    name = "generate"

    def run(self, context) -> NodeResult:
        messages = self.messages(context.payload())
        message = self.client.complete(messages)
        return NodeResult(self.name, self.parse_with_correction(messages, message, validate_generation))
