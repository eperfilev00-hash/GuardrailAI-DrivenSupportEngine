from typing import Optional, AsyncIterator, Any
from openai import AsyncOpenAI
# Импортируем правильный тип для сообщений
from openai.types.chat import ChatCompletionMessageParam

from app.config import settings
from app.llm.base import BaseLLM


class OpenAILLM(BaseLLM):
    """OpenAI LLM provider implementation."""

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model_name = settings.llm_model

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        context: Optional[str] = None,
        **kwargs: Any  # Явно указываем тип для kwargs
    ) -> str:
        """Generate a complete response from OpenAI."""
        messages = self._build_messages(prompt, system_prompt, context)

        # Передаем строго именованные аргументы
        response = await self.client.chat.completions.create(
            model=self.model_name,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            messages=messages,
            **kwargs
        )
        return response.choices[0].message.content

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        context: Optional[str] = None,
        **kwargs: Any
    ) -> AsyncIterator[str]:
        """Streaming generation using OpenAI's streaming API."""
        messages = self._build_messages(prompt, system_prompt, context)

        stream = await self.client.chat.completions.create(
            model=self.model_name,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            messages=messages,
            stream=True,
            **kwargs
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                # В современных версиях SDK используем chunk.choices[0].delta.content
                yield chunk.choices[0].delta.content
                
    def _build_messages(
        self,
        prompt: str,
        system_prompt: str = "",
        context: Optional[str] = None
    ) -> list[ChatCompletionMessageParam]:
        """Build messages array for OpenAI API."""
        messages: list[ChatCompletionMessageParam] = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        user_content = prompt
        if context:
            user_content = f"Context:\n{context}\n\nUser Question: {prompt}"

        messages.append({"role": "user", "content": user_content})

        return messages

    async def get_model_name(self) -> str:
        """Get the current model name."""
        return self.model_name