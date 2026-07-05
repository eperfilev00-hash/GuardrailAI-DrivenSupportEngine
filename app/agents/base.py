from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from app.llm.factory import get_llm
from app.llm.base import BaseLLM

@dataclass
class AgentResponse:
    """Standardized agent response."""

    response: str
    agent_type: str
    confidence: float = 1.0
    context: dict = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    requires_human: bool = False

class BaseAgent(ABC):
    """Abstract base class for all support agents."""

    agent_type: str = "base"
    description: str = "Base agent"
    system_prompt: str = ""

    @abstractmethod
    async def process(self, message: str, context: dict) -> AgentResponse:
        pass

    async def generate_response(
        self, 
        prompt: str, 
        system_prompt: str = "",
        rag_context: Optional[str] = None
    ) -> str:
        llm: BaseLLM = await get_llm()
        
        if not system_prompt:
            system_prompt = self.system_prompt
            
        return await llm.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            context=rag_context
        )

    @abstractmethod
    async def can_handle(self, message: str) -> float:
        pass
    
    async def generate_response_stream(
            self,
            prompt: str,
            system_prompt: Optional[str] = None,
            rag_context: str = ""
        ) -> AsyncIterator[str]:
            """
            Генерирует ответ потоком (токен за токеном).
            НЕ используйте await при вызове llm.generate_stream()! 
            НЕ используйте await при вызове этого метода!             
            Правильное использование:
            
                async for token in agent.generate_response_stream(prompt="Hello"):
                    print(token)
            """
            llm: BaseLLM = await get_llm()
            
            if system_prompt is None:
                system_prompt = self.system_prompt
            
            # ДОБАВЛЯЕМ await перед llm.generate_stream
            stream = await llm.generate_stream(
                prompt=prompt,
                system_prompt=system_prompt,
                context=rag_context
            )
            
            # Теперь stream — это действительно AsyncIterator, и по нему можно итерироваться
            async for token in stream:
                yield token