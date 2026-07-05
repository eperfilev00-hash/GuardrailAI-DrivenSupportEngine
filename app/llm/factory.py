from app.config import settings
from app.llm.base import BaseLLM

# Импортируем здесь, чтобы избежать циклических импортов
from app.llm.anthropic import AnthropicLLM
from app.llm.openai import OpenAILLM

async def get_llm() -> BaseLLM:
    """
    Factory function to get the configured LLM provider.
    """
    provider = settings.llm_provider.lower()
    
    if provider == "anthropic":
        return AnthropicLLM()
    elif provider == "openai":
        return OpenAILLM()
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")