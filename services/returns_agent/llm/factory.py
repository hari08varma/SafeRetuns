from returns_agent.config import Settings
from returns_agent.llm.client import LLMClient
from returns_agent.llm.deepseek import DeepSeekProvider
from returns_agent.llm.metering import MeteredClient


def build_llm_client(settings: Settings) -> LLMClient | None:
    """None means no model: LLM nodes use structured input and replies use templates."""
    if settings.llm_provider == "deepseek":
        return MeteredClient(
            DeepSeekProvider(
                api_key=settings.deepseek_api_key,
                model=settings.llm_model,
                base_url=settings.deepseek_base_url,
                timeout_s=settings.llm_timeout_s,
            )
        )
    return None
