# app/llm/anthropic.py
import anthropic
from typing import Optional, AsyncIterator
from app.config import settings
from app.llm.base import BaseLLM

class AnthropicLLM(BaseLLM):
    """Anthropic LLM provider implementation."""

    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model_name = settings.llm_model

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        context: Optional[str] = None,
        **kwargs
    ) -> str:
        full_prompt = prompt
        if context:
            full_prompt = f"Context:\n{context}\n\nUser Question: {prompt}"

        message = await self.client.messages.create(
            model=self.model_name,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            system=system_prompt,
            messages=[
                {"role": "user", "content": full_prompt}
            ],
            **kwargs
        )
        return message.content[0].text

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        context: Optional[str] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Streaming generation using Anthropic's streaming API."""
        full_prompt = prompt
        if context:
            full_prompt = f"Context:\n{context}\n\nUser Question: {prompt}"

        async with self.client.messages.stream(
            model=self.model_name,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            system=system_prompt,
            messages=[
                {"role": "user", "content": full_prompt}
            ],
            **kwargs
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def get_model_name(self) -> str:
        return self.model_name