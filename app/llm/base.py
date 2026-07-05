# app/llm/base.py
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

class BaseLLM(ABC):
    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        context: Optional[str] = None
    ) -> str:
        """Генерирует полный ответ."""
        pass
    
    @abstractmethod
    async def generate_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        context: Optional[str] = None
    ) -> AsyncIterator[str]:
        """
        Асинхронный генератор для потоковой генерации.
        
        Использование:
            async for token in llm.generate_stream(prompt="Hello"):
                print(token)
        
        НЕ используйте await при вызове! 
        """
        pass

    @abstractmethod
    async def get_model_name(self) -> str:
        """Return the name of the currently active model."""
        pass