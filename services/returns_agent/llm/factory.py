from returns_agent.config import Settings
from returns_agent.llm.client import LLMClient
from returns_agent.llm.deepseek import DeepSeekProvider
from returns_agent.llm.fake import FakeProvider


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "deepseek":
        return DeepSeekProvider(
            api_key=settings.deepseek_api_key,
            model=settings.llm_model,
            base_url=settings.deepseek_base_url,
            timeout_s=settings.llm_timeout_s,
        )
    return FakeProvider()
