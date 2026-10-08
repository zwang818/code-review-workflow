"""Errors that can be presented to a user without a traceback."""


class AgentError(Exception):
    """Base class for expected application failures."""


class ConfigError(AgentError):
    """Missing or invalid configuration."""


class ModelError(AgentError):
    """HTTP, protocol or model response failure."""


class ToolError(AgentError):
    """Invalid tool arguments or an unreadable target."""


class ContractError(AgentError):
    """A node result does not satisfy its declared contract."""


class BudgetExceeded(AgentError):
    """The workflow cannot spend another logical model request."""
