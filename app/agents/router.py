import logging
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional
from pydantic import BaseModel, Field

from app.agents.base import BaseAgent, AgentResponse
from app.agents.agents import GeneralAgent, OrderAgent, ReturnAgent, TechnicalAgent

logger = logging.getLogger(__name__) 

class RouterAgent(BaseAgent):
    """
    Router Agent: Determines which specialist should handle the request.
    """
    
    agent_type = "router"
    
    specialists: List[BaseAgent] = [
        GeneralAgent(),
        OrderAgent(),
        ReturnAgent(),
        TechnicalAgent()
    ]

    ROUTING_SYSTEM_PROMPT = """
    You are a support ticket router. 
    Your goal is to classify the user's request and assign it to the most appropriate agent.
    
    Available Agents:
    1. general: For general inquiries, greetings, or questions not fitting other categories.
    2. order: For order status, tracking, delivery, and shipping questions.
    3. return: For product returns, refunds, and exchange requests.

    Return ONLY the agent name (e.g., "general", "order", "return") based on the user's input.
    Do not add any explanation.
    """

    async def process(self, message: str, context: dict) -> AgentResponse:
        assigned_agent_name = await self._route_message(message)
        agent = self._get_agent_by_name(assigned_agent_name)
        
        if not agent:
            agent = GeneralAgent()
            assigned_agent_name = "general"

        response = await agent.process(message, context)
        response.agent_type = assigned_agent_name
        
        return response

    async def _route_message(self, message: str) -> str:
        agent_names = [spec.agent_type for spec in self.specialists]
        
        prompt = (
            f"Available agents: {', '.join(agent_names)}\n"
            f"User message: {message}"
        )
        
        raw_response = await self.generate_response(
            prompt=prompt,
            system_prompt=self.ROUTING_SYSTEM_PROMPT
        )
        
        return raw_response.strip().lower().replace('"', '').replace("'", "")

    def _get_agent_by_name(self, name: str) -> Optional[BaseAgent]:
        for agent in self.specialists:
            if agent.agent_type == name:
                return agent
        return None

    async def can_handle(self, message: str) -> float:
        return 1.0


async def route_request(
    session_id: str,
    message: str,
    user_id: Optional[str] = None,
    metadata: Optional[dict] = None
) -> AgentResponse:
    context = {
        "session_id": session_id,
        "user_id": user_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        **(metadata or {})
    }

    router_agent = RouterAgent()
    agent_response = await router_agent.process(message=message, context=context)
    
    return agent_response

async def route_request_stream(
    session_id: str,
    message: str,
    user_id: Optional[str] = None,
    metadata: Optional[dict] = None
) -> AsyncIterator[dict]:
    """
    Асинхронный генератор для потоковой обработки запросов поддержки.
    """
    context = {
        "session_id": session_id,
        "user_id": user_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "streaming": True,
        **(metadata or {})
    }

    try:
        router_agent = RouterAgent()
        
        # 1. Определяем агента для запроса
        assigned_agent_name = await router_agent._route_message(message)
        agent = router_agent._get_agent_by_name(assigned_agent_name)
        
        if not agent:
            agent = GeneralAgent()
            assigned_agent_name = "general"

        # 2. Уведомляем клиента о выбранном агенте
        yield {
            "type": "routing",
            "agent_type": assigned_agent_name,
            "session_id": session_id,
        }

        # 3. Проверяем, поддерживает ли агент стриминг
        # ★★★ ИСПРАВЛЕНИЕ: Проверяем тип возвращаемого значения ★★★
        if hasattr(agent, 'generate_response_stream'):
            # Проверяем, является ли метод асинхронным генератором
            import inspect
            stream_method = getattr(agent, 'generate_response_stream')
            
            # Если это async generator function, то можно использовать
            if inspect.isasyncgenfunction(stream_method):
                full_response = ""
                
                # Вызываем метод (возвращает async generator)
                stream = stream_method(
                    prompt=message,
                    rag_context=context.get("rag_context", "")
                )
                
                # Итерируемся
                async for token in stream:
                    full_response += token
                    yield {
                        "type": "token",
                        "content": token,
                        "session_id": session_id,
                    }
                
                yield {
                    "type": "complete",
                    "agent_type": assigned_agent_name,
                    "response": full_response,
                    "confidence": 0.9,
                    "session_id": session_id,
                }
            else:
                # Метод есть, но это не async generator — используем fallback
                logger.warning(f"Agent {agent.agent_type} has generate_response_stream but it's not an async generator. Using fallback.")
                agent_response = await agent.process(message=message, context=context)
                
                yield {
                    "type": "token",
                    "content": agent_response.response,
                    "session_id": session_id,
                }
                
                yield {
                    "type": "complete",
                    "agent_type": agent_response.agent_type,
                    "response": agent_response.response,
                    "confidence": agent_response.confidence,
                    "sources": agent_response.sources,
                    "session_id": session_id,
                }
        else:
            # Нет метода generate_response_stream — используем process
            agent_response = await agent.process(message=message, context=context)
            
            yield {
                "type": "token",
                "content": agent_response.response,
                "session_id": session_id,
            }
            
            yield {
                "type": "complete",
                "agent_type": agent_response.agent_type,
                "response": agent_response.response,
                "confidence": agent_response.confidence,
                "sources": agent_response.sources,
                "session_id": session_id,
            }

    except Exception as e:
        logger.error(
            f"Streaming request failed for session {session_id}: {str(e)}",
            exc_info=True
        )
        yield {
            "type": "error",
            "message": str(e),
            "session_id": session_id,
        }