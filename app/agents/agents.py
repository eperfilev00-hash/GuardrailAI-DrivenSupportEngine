"""Specialized support agents."""

from typing import Optional
import logging

from app.agents.base import BaseAgent, AgentResponse
from app.services.orders import OrderService, ReturnService

logger = logging.getLogger(__name__)

class GeneralAgent(BaseAgent):
    """
    General-purpose agent for queries that don't fit specialized categories.
    
    Handles greetings, general questions, and fallback cases.
    """
    
    agent_type = "general"
    description = "Handles general inquiries, greetings, and fallback cases"
    system_prompt = """
    Ты дружелюбный помощник службы поддержки.
    Отвечай на вопросы пользователей вежливо и полезно.
    Если ты не знаешь ответа, честно скажи об этом и предложи связаться с оператором.
    """

    async def can_handle(self, message: str) -> float:
        # GeneralAgent обрабатывает всё, что не попало в другие категории
        # Возвращаем низкий приоритет, чтобы специализированные агенты имели преимущество
        return 0.5

    async def process(self, message: str, context: dict) -> AgentResponse:
        response_text = await self.generate_response(
            prompt=message,
            system_prompt=self.system_prompt,
            rag_context=context.get("rag_context", "")
        )

        return AgentResponse(
            response=response_text,
            agent_type=self.agent_type,
            confidence=0.7,  # Средняя уверенность для общих запросов
            sources=[],
            context={},
        )

class OrderAgent(BaseAgent):
    """Agent for order-related queries."""

    agent_type = "order"
    description = "Handles order status, tracking, and delivery questions"
    system_prompt = """
    Ты помощник по вопросам заказов. 
    Используй данные из базы заказов для предоставления точной информации.
    Будь вежлив и конкретен. Если заказа не существует, Сообщи об этом.
    
    ВАЖНО: Ты можешь показывать информацию только о заказах текущего пользователя.
    Если пользователь запрашивает чужой заказ — откажи и объясни, что это нарушает конфиденциальность.
    """

    async def can_handle(self, message: str) -> float:
        keywords = ["заказ", "доставк", "трек", "статус", "order", "delivery"]
        score = sum(1 for kw in keywords if kw.lower() in message.lower())
        return min(score / 3, 1.0)

    async def process(self, message: str, context: dict) -> AgentResponse:
        order_number = context.get("order_number") or self._extract_order_number(message)
        user_id = context.get("user_id")
        
        # ПРОВЕРКА: user_id должен быть указан в контексте
        if not user_id:
            logger.warning("OrderAgent: user_id not provided in context")
            return AgentResponse(
                response="Не удалось определить пользователя. Пожалуйста, войдите в систему.",
                agent_type=self.agent_type,
                confidence=0.3,
                sources=["auth_check"],
                context={},
            )

        order_data = None
        has_access = False
        access_denied = False
        
        if order_number:
            # ПРОВЕРКА ПРАВ ДОСТУПА
            order_dto, has_access = await OrderService.get_by_order_number_with_auth(
                order_number, int(user_id)
            )
            
            if order_dto:
                if has_access:
                    # Пользователь имеет доступ к заказу
                    items_safe = self._sanitize_for_llm(order_dto.items)
                    order_data = {
                        "order_number": order_dto.order_number,
                        "status": order_dto.status,
                        "total_amount": order_dto.total_amount,
                        "tracking_number": order_dto.tracking_number,
                        "created_at": order_dto.created_at.isoformat(),
                        "items": items_safe,
                    }
                else:
                    # Пользователь не имеет доступа к заказу
                    access_denied = True
                    logger.warning(
                        f"Access denied for order {order_number}",
                        extra={"user_id": user_id, "order_number": order_number}
                    )
            else:
                # Заказ не найден
                pass

        if order_data:
            db_context = f"""
            Данные о заказе:
            - Номер: {order_data['order_number']}
            - Статус: {order_data['status']}
            - Сумма: {order_data['total_amount']} руб.
            - Трек-номер: {order_data['tracking_number'] or 'не назначен'}
            - Дата: {order_data['created_at']}
            """
        elif access_denied:
            db_context = "У вас нет доступа к этому заказу. Вы можете查看 только свои заказы."
        else:
            db_context = "Заказ не найден. Попроси пользователя проверить номер заказа или войти в систему."

        response_text = await self.generate_response(
            prompt=message,
            system_prompt=self.system_prompt,
            rag_context=db_context
        )

        return AgentResponse(
            response=response_text,
            agent_type=self.agent_type,
            confidence=0.9 if order_data else (0.3 if access_denied else 0.7),
            sources=["orders_db"],
            context={"order": order_data, "access_denied": access_denied},
        )

    def _extract_order_number(self, message: str) -> Optional[str]:
        """Extract order number from message (e.g., 'ORD-12345')."""
        import re
        match = re.search(r'(?:ORD-|ORDER-|#)?(\d{5,})', message, re.IGNORECASE)
        if match:
            return f"ORD-{match.group(1)}" if not match.group(0).startswith("ORD") else match.group(0)
        return None

    def _sanitize_for_llm(self, data: dict, max_keys: int = 20) -> dict:
        """
        БЕЗОПАСНОСТЬ: Ограничивает размер данных для передачи в LLM 
        
        Защищает от:
        - Передачи огромных JSON в LLM (токены = деньги)
        - Утечки чувствительных данных через metadata
        """
        if not isinstance(data, dict):
            return data
        
        sanitized = {}
        for i, (key, value) in enumerate(data.items()):
            if i >= max_keys:
                break
            
            # Пропускаем подозрительные ключи
            if key.startswith("_") or "secret" in key.lower() or "password" in key.lower():
                logger.warning(f"Skipped sensitive key in LLM context: {key}")
                continue
            
            # Ограничиваем размер строк
            if isinstance(value, str) and len(value) > 500:
                sanitized[key] = value[:500] + "... (truncated)"
            else:
                sanitized[key] = value
        
        return sanitized


class ReturnAgent(BaseAgent):
    """Agent for return and refund queries."""

    agent_type = "return"
    description = "Handles product returns and refund requests"
    system_prompt = """
    Ты помощник по вопросам возвратов.
    Используй данные из базы возвратов для предоставления информации.
    Объясняй процедуру возврата четко и пошагово.
    
    ВАЖНО: Ты можешь показывать информацию только о возвратах текущего пользователя.
    Если пользователь запрашивает чужой возврат — откажи и объясни, что это нарушает конфиденциальность.
    """

    async def can_handle(self, message: str) -> float:
        keywords = ["возврат", "вернуть", "рефанд", "return", "refund"]
        score = sum(1 for kw in keywords if kw.lower() in message.lower())
        return min(score / 3, 1.0)

    async def process(self, message: str, context: dict) -> AgentResponse:
        return_number = context.get("return_number") or self._extract_return_number(message)
        order_number = context.get("order_number")
        user_id = context.get("user_id")
        
        # ПРОВЕРКА: user_id должен быть указан в контексте
        if not user_id:
            logger.warning("ReturnAgent: user_id not provided in context")
            return AgentResponse(
                response="Не удалось определить пользователя. Пожалуйста, войдите в систему.",
                agent_type=self.agent_type,
                confidence=0.3,
                sources=["auth_check"],
                context={},
            )

        return_data = None
        order_data = None
        has_access = False
        access_denied = False
        
        if return_number:
            # ПРОВЕРКА ПРАВ ДОСТУПА
            return_dto, has_access = await ReturnService.get_by_return_number_with_auth(
                return_number, int(user_id)
            )
            
            if return_dto:
                if has_access:
                    # Пользователь имеет доступ к возврату
                    return_data = {
                        "return_number": return_dto.return_number,
                        "status": return_dto.status,
                        "reason": return_dto.reason,
                        "refund_amount": return_dto.refund_amount,
                        "comment": return_dto.comment,
                    }
                else:
                    # Пользователь не имеет доступа к возврату
                    access_denied = True
                    logger.warning(
                        f"Access denied for return {return_number}",
                        extra={"user_id": user_id, "return_number": return_number}
                    )
            else:
                # Возврат не найден, пробуем найти по order_number
                if order_number:
                    order = await OrderService.get_by_order_number(order_number)
                    if order:
                        order_data = {
                            "order_number": order.order_number,
                            "status": order.status,
                            "total_amount": order.total_amount,
                        }
        else:
            # Если нет return_number, пытаемся найти по order_number
            if order_number:
                order = await OrderService.get_by_order_number(order_number)
                if order:
                    # Проверяем, что пользователь является владельцем заказа
                    if order.user_id == int(user_id):
                        order_data = {
                            "order_number": order.order_number,
                            "status": order.status,
                            "total_amount": order.total_amount,
                        }
                    else:
                        access_denied = True

        if return_data:
            db_context = f"""
            Данные о возврате:
            - Номер: {return_data['return_number']}
            - Статус: {return_data['status']}
            - Причина: {return_data['reason']}
            - Сумма возврата: {return_data['refund_amount']} руб.
            - Комментарий: {return_data['comment'] or 'нет'}
            """
        elif order_data:
            db_context = f"""
            Заказ найден, но возвратов не оформлено.
            Номер заказа: {order_data['order_number']}
            Статус: {order_data['status']}
            
            Для оформления возврата укажите причину и товары.
            """
        elif access_denied:
            db_context = "У вас нет доступа к этому возврату. Вы можете смотреть только свои возвраты."
        else:
            db_context = "Возврат не найден. Попроси пользователя проверить номер возврата или войти в систему."

        response_text = await self.generate_response(
            prompt=message,
            system_prompt=self.system_prompt,
            rag_context=db_context
        )

        return AgentResponse(
            response=response_text,
            agent_type=self.agent_type,
            confidence=0.85 if return_data else (0.3 if access_denied else 0.6),
            sources=["returns_db"],
            context={"return": return_data, "order": order_data, "access_denied": access_denied},
        )

    def _extract_return_number(self, message: str) -> Optional[str]:
        """Extract return number from message."""
        import re
        match = re.search(r'(?:RET-|RETURN-|#)?(\d{5,})', message, re.IGNORECASE)
        if match:
            return f"RET-{match.group(1)}" if not match.group(0).startswith("RET") else match.group(0)
        return None


class TechnicalAgent(BaseAgent):
    """Agent for technical issues and bug reports."""

    agent_type = "technical"
    description = "Handles bugs, errors, technical issues, and troubleshooting"
    system_prompt = """
    Ты технический специалист службы поддержки.
    Твоя задача — помогать пользователям с техническими проблемами:
    - Ошибки в приложении или на сайте
    - Проблемы с авторизацией и доступом
    - Баги и некорректная работа функционала
    - Технические вопросы по интеграции
    
    Действия:
    1. Вежливо уточни детали проблемы (что происходит, когда возникает ошибка)
    2. Предложи пошаговое решение или обходной путь
    3. Если проблема требует вмешательства разработчиков — создай тикет и Сообщи пользователю номер
    4. Зафиксируй шаги для воспроизведения бага
    
    Будь терпелив и избегай сложного жаргона.
    """

    async def can_handle(self, message: str) -> float:
        keywords = [
            "ошибк", "баг", "не работ", "слом", "глюк",
            "error", "bug", "crash", "fail", "issue", "problem",
            "технич", "неправильн", "некорректн"
        ]
        score = sum(1 for kw in keywords if kw.lower() in message.lower())
        return min(score / 2, 1.0)

    async def process(self, message: str, context: dict) -> AgentResponse:
        # Собираем контекст для технической проблемы
        user_id = context.get("user_id")
        session_id = context.get("session_id")
        
        # Здесь можно добавить интеграцию с системой тикетов (например, Jira, GitHub Issues)
        # Для примера — заглушка
        ticket_data = None
        
        if ticket_data:
            db_context = f"""
            Данные о тикете:
            - Номер: {ticket_data['ticket_id']}
            - Статус: {ticket_data['status']}
            - Приоритет: {ticket_data['priority']}
            - Назначен: {ticket_data['assignee'] or 'не назначен'}
            """
        else:
            db_context = """
            Новый технический запрос. 
            Необходимо:
            1. Уточнить шаги для воспроизведения
            2. Определить критичность проблемы
            3. Предложить временное решение если возможно
            """

        response_text = await self.generate_response(
            prompt=message,
            system_prompt=self.system_prompt,
            rag_context=db_context
        )

        return AgentResponse(
            response=response_text,
            agent_type=self.agent_type,
            confidence=0.85 if ticket_data else 0.7,
            sources=["technical_kb"],
            context={"ticket": ticket_data, "user_id": user_id, "session_id": session_id},
        )

    def _extract_error_details(self, message: str) -> dict:
        """Extract error codes and technical details from message."""
        import re
        
        error_codes = re.findall(r'\b[A-Z]{2,}-\d{3,}\b', message)
        stack_traces = re.search(r'(?:Traceback|Error:|Exception:).*?(?=\n\n|\Z)', message, re.DOTALL)
        
        return {
            "error_codes": error_codes,
            "has_stack_trace": bool(stack_traces),
            "message_length": len(message),
        }