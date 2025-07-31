import os
import logging
from enum import Enum
from dataclasses import dataclass, fields
from typing import Any, Optional, Dict, get_origin, get_args

from langchain_core.runnables import RunnableConfig
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class SearchAPI(Enum):
    """Enum for different search APIs."""
    SEARXNG = "searxng"


def _coerce_field_value(field_type: Any, raw_value: Any) -> Any:
    """Coerce raw_value (from env or runnable config) to the annotated field_type."""
    if raw_value is None:
        return None

    origin = get_origin(field_type)
    args = get_args(field_type)

    # Handle Optional[...] (e.g., Optional[str])
    if origin is Optional and args:
        return _coerce_field_value(args[0], raw_value)

    # Enum handling
    if isinstance(field_type, type) and issubclass(field_type, Enum):
        if isinstance(raw_value, field_type):
            return raw_value
        if isinstance(raw_value, str):
            try:
                return field_type(raw_value)
            except ValueError:
                try:
                    return field_type[raw_value]
                except (KeyError, ValueError):
                    pass
        return raw_value  # fallback; will likely fail later if invalid

    # Primitive types
    if field_type is int:
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return raw_value
    if field_type is float:
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return raw_value
    if field_type is bool:
        if isinstance(raw_value, str):
            return raw_value.lower() in ("1", "true", "yes", "on")
        return bool(raw_value)

    # Default: return as-is (e.g., str, dict, etc.)
    return raw_value


@dataclass(kw_only=True)
class Configuration:
    """The configurable fields for the agentic retrieval."""

    # Graph-specific configuration
    number_of_queries: int = 4  # Number of search queries to generate per iteration
    max_search_depth: int = 2  # Maximum number of reflection + search iterations
    recursion_limit: int = 30  # Maximum recursion limit for the search
    search_api: SearchAPI = SearchAPI.SEARXNG

    supervisor_model: str = os.getenv("SUPERVISOR_MODEL", "openai:gpt-4.1-mini")  # Model for supervisor agent
    researcher_model: str = os.getenv("RESEARCHER_MODEL", "openai:gpt-4.1-mini")  # Model for research agents

    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> "Configuration":
        """Create a Configuration instance from a RunnableConfig and environment variables."""
        configurable = (
            config["configurable"] if config and "configurable" in config else {}
        )
        raw_values: dict[str, Any] = {}
        for f in fields(cls):
            if not f.init:
                continue
            # Environment variable takes precedence, then runnable config
            env_val = os.environ.get(f.name.upper(), None)
            cfg_val = configurable.get(f.name)
            chosen = env_val if env_val is not None else cfg_val
            coerced = _coerce_field_value(f.type, chosen)
            if coerced is not None and coerced != "":
                raw_values[f.name] = coerced

        return cls(**raw_values)


# preserve the original side-effect print from the prior version
print("SUPERVISOR_MODEL: ", os.getenv("SUPERVISOR_MODEL", "openai:gpt-4.1-mini"))
print("RESEARCHER_MODEL: ", os.getenv("RESEARCHER_MODEL", "openai:gpt-4.1-mini"))