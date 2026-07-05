"""Guardrails response validator with ML-based toxicity detection."""

import asyncio
import re
import time
import threading
import logging
from dataclasses import dataclass
from typing import Optional
from concurrent.futures import ThreadPoolExecutor 

logger = logging.getLogger(__name__)

from app.guardrails.rules import DEFAULT_RULES, GuardrailRule, RuleType


# ============================================================
# ML-BASED TOXICITY DETECTION
# ============================================================

class ToxicityDetector:
    """
    ML-based toxicity detector using Detoxify model.
    
    Supports multiple languages (English, Russian, Spanish).
    Caches model to avoid reloading on every request.
    """
    
    _instance: Optional["ToxicityDetector"] = None
    _model = None
    _initialized: bool = False
    
    # Порог токсичности (0.0 - 1.0)
    TOXICITY_THRESHOLD = 0.5
    
    # ThreadPoolExecutor для CPU-тяжёлых ML-вычислений 
    _executor: Optional[ThreadPoolExecutor] = None
    
    # ============================================================
    # КЭШИРОВАНИЕ РЕЗУЛЬТАТОВ
    # ============================================================
    
    # Время жизни кэша в секундах (по умолчанию 1 час)
    CACHE_TTL = 3600
    
    # Кэш: { normalized_text: (result_dict, timestamp) }
    _cache: dict[str, tuple[dict, float]] = {}
    
    # Блокировка для потокобезопасного доступа к кэшу
    _cache_lock: threading.Lock = threading.Lock()
    
    # Статистика кэша
    _cache_hits: int = 0
    _cache_misses: int = 0
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Создаём executor один раз при первом создании
            cls._executor = ThreadPoolExecutor(max_workers=2)
        return cls._instance
    
    @classmethod
    def _normalize_text(cls, text: str) -> str:
        """Нормализует текст для использования в качестве ключа кэша."""
        # Удаляем лишние пробелы и приводим к нижнему регистру
        return " ".join(text.lower().split())
    
    @classmethod
    def _get_from_cache(cls, text: str) -> Optional[dict]:
        """
        Получает результат из кэша, если запись не устарела.
        
        Args:
            text: Текст для поиска в кэше
            
        Returns:
            Результат из кэша или None если не найдено/устарело
        """
        normalized = cls._normalize_text(text)
        
        with cls._cache_lock:
            if normalized in cls._cache:
                cached_result, timestamp = cls._cache[normalized]
                if time.time() - timestamp < cls.CACHE_TTL:
                    cls._cache_hits += 1
                    return cached_result
                else:
                    # Удаляем устаревшую запись
                    del cls._cache[normalized]
            
            cls._cache_misses += 1
            return None
    
    @classmethod
    def _save_to_cache(cls, text: str, result: dict) -> None:
        """
        Сохраняет результат в кэш.
        
        Args:
            text: Текст для кэширования
            result: Результат проверки токсичности
        """
        normalized = cls._normalize_text(text)
        
        with cls._cache_lock:
            cls._cache[normalized] = (result, time.time())
    
    @classmethod
    def _clear_expired_cache(cls) -> int:
        """
        Очищает устаревшие записи из кэша.
        
        Returns:
            Количество удалённых записей
        """
        current_time = time.time()
        removed_count = 0
        
        with cls._cache_lock:
            expired_keys = [
                key for key, (_, timestamp) in cls._cache.items()
                if current_time - timestamp >= cls.CACHE_TTL
            ]
            for key in expired_keys:
                del cls._cache[key]
                removed_count += 1
        
        return removed_count
    
    @classmethod
    def clear_cache(cls) -> None:
        """Полностью очищает кэш."""
        with cls._cache_lock:
            cls._cache.clear()
            cls._cache_hits = 0
            cls._cache_misses = 0
    
    @classmethod
    def get_cache_stats(cls) -> dict:
        """
        Возвращает статистику кэша.
        
        Returns:
            Dict с информацией о кэше
        """
        with cls._cache_lock:
            total_requests = cls._cache_hits + cls._cache_misses
            hit_rate = (cls._cache_hits / total_requests * 100) if total_requests > 0 else 0.0
            
            return {
                "size": len(cls._cache),
                "hits": cls._cache_hits,
                "misses": cls._cache_misses,
                "hit_rate": round(hit_rate, 2),
                "ttl_seconds": cls.CACHE_TTL,
            }
    
    @classmethod
    async def initialize(cls):
        """Initialize the Detoxify model (call once at app startup)."""
        if cls._initialized:
            return
        
        try:
            from detoxify import Detoxify
            cls._model = Detoxify("multilingual")
            cls._initialized = True
        except ImportError:
            try:
                from detoxify import Detoxify
                cls._model = Detoxify("original")
                cls._initialized = True
            except Exception as e:
                logger.warning(f"Failed to load Detoxify model: {e}. Using regex fallback.")
                cls._initialized = True
        except Exception as e:
            logger.warning(f"Failed to initialize toxicity detector: {e}")
            cls._initialized = True
    
    @classmethod
    def is_ready(cls) -> bool:
        """Check if model is loaded and ready."""
        return cls._initialized and cls._model is not None
    
    @classmethod
    async def detect(cls, text: str) -> dict:
        """
        Detect toxicity in text.
        
        Использует кэширование для одинаковых текстов.
        """
        if not cls._initialized:
            await cls.initialize()
        
        # Проверяем кэш
        cached_result = cls._get_from_cache(text)
        if cached_result is not None:
            return cached_result
        
        if cls._model is None:
            result = cls._fallback_detection(text)
            cls._save_to_cache(text, result)
            return result
        
        try:
            # ЗАПУСК В ОТДЕЛЬНОМ ПОТОКЕ 
            loop = asyncio.get_event_loop()
            scores = await loop.run_in_executor(cls._executor, cls._model.predict, text)
            # Сохраняем результат в кэш
            cls._save_to_cache(text, scores)
            return scores
        except Exception as e:
            logger.warning(f"Toxicity detection failed: {e}")
            result = cls._fallback_detection(text)
            cls._save_to_cache(text, result)
            return result
    
    @classmethod
    def _fallback_detection(cls, text: str) -> dict:
        """Regex-based fallback when ML model is unavailable."""
        toxic_patterns = [
            r"\b(убий|умри|сдохн|заткн|твар|мраз|сук[аи]|бл[ья]|еб[ать|ано])\b",
            r"\b(kill|die|shut up|bitch|fuck|shit|asshole)\b",
        ]
        
        text_lower = text.lower()
        is_toxic = any(re.search(p, text_lower) for p in toxic_patterns)
        
        return {
            "toxicity": 0.9 if is_toxic else 0.1,
            "severe_toxicity": 0.8 if is_toxic else 0.0,
            "obscene": 0.7 if is_toxic else 0.0,
            "threat": 0.5 if "умри" in text_lower or "kill" in text_lower else 0.0,
            "insult": 0.6 if is_toxic else 0.0,
            "identity_attack": 0.0,
        }
    
    @classmethod
    async def is_toxic(cls, text: str, threshold: float | None = None) -> bool:
        """
        Check if text is toxic based on threshold.
        
        Args:
            text: Text to analyze
            threshold: Override default TOXICITY_THRESHOLD
            
        Returns:
            True if text is toxic, False otherwise
        """
        if threshold is None:
            threshold = cls.TOXICITY_THRESHOLD
        
        scores = await cls.detect(text)
        return scores.get("toxicity", 0.0) > threshold


# ============================================================
# PROFANITY DETECTION
# ============================================================

BAD_WORDS = {
    # Русские
    "сука", "суки", "сучара", "сучонок",
    "блядь", "бля", "бляд",
    "пизд", "пиздец", "пиздить",
    "ебать", "ебало", "ебан",
    "хуй", "хуя", "хуе", "хуём",
    "мудак", "мудаки", "мудачьё",
    "уёбок", "уебки",
    "говно", "говна", "говнюк",
    "залуп", "залупа",
    # Английские
    "fuck", "fucking", "fucked",
    "shit", "shitty",
    "bitch", "bitches",
    "asshole", "ass",
    "damn", "dammit",
    "bastard", "bastards",
    # Дополнительные (можно расширять)
    "плохое", "слово", "пример",
}

# ============================================================
# VALIDATOR
# ============================================================

@dataclass
class ValidationResult:
    """Result of guardrails validation."""

    is_valid: bool
    failed_rules: list[str]
    reason: Optional[str] = None
    suggestions: list[str] | None = None
    toxicity_scores: dict | None = None

    def __post_init__(self):
        if self.suggestions is None:
            self.suggestions = []


class ResponseValidator:
    """Validates AI responses against guardrail rules."""

    def __init__(self, rules: list[GuardrailRule] | None = None):
        self.rules = rules or DEFAULT_RULES
        self.toxicity_detector = ToxicityDetector()

    async def validate(
        self,
        response: str,
        context: dict | None = None,
    ) -> ValidationResult:
        """
        Validate response against all enabled rules.
        
        Returns ValidationResult with pass/fail status.
        """
        failed_rules = []
        reasons = []
        toxicity_scores = None

        for rule in self.rules:
            if not rule.enabled:
                continue

            is_passed = await self._check_rule(rule, response, context or {})
            if not is_passed:
                failed_rules.append(rule.name)
                reasons.append(rule.description)
                
                # Collect toxicity scores for debugging
                if rule.rule_type == RuleType.TOXICITY:
                    toxicity_scores = await self.toxicity_detector.detect(response)

        is_valid = len(failed_rules) == 0

        return ValidationResult(
            is_valid=is_valid,
            failed_rules=failed_rules,
            reason="; ".join(reasons) if reasons else None,
            toxicity_scores=toxicity_scores,
        )

    async def _check_rule(
        self,
        rule: GuardrailRule,
        response: str,
        context: dict,
    ) -> bool:
        """Check single rule against response."""
        if rule.rule_type == RuleType.TOXICITY:
            return await self._check_toxicity_ml(response)
            
        if rule.rule_type == RuleType.PROFANITY:
            return self._check_profanity(response)
        elif rule.rule_type == RuleType.PII_LEAK:
            return self._check_pii(response)
        elif rule.rule_type == RuleType.HALLUCINATION:
            return self._check_hallucination(response, context)
        
        return True

    async def _check_toxicity_ml(self, response: str) -> bool:
        """
        Проверяет ответ на токсичность с помощью ML-модели Detoxify.
        
        Поддерживает мультиязычный анализ (русский + английский).
        Возвращает True если текст НЕ токсичен, False если токсичен.
        """
        is_toxic = await self.toxicity_detector.is_toxic(response)
        
        if is_toxic:
            scores = await self.toxicity_detector.detect(response)
            logger.warning(
                "Toxic content detected",
                extra={
                    "toxicity_score": scores.get("toxicity", 0.0),
                    "categories": scores,
                }
            )
            return False
        
        return True

    def _check_profanity(self, response: str) -> bool:
        """
        Проверяет ответ на ненормативную лексику.
        Убирает точки и нижние подчеркивания, чтобы найти "су_ка" или "сук.а".
        """
        # 1. Очищаем текст: удаляем '.', '_' и '-', но оставляем пробелы
        normalized = re.sub(r'[\._\-]+', '', response)
        
        # 2. Проверяем каждое стоп-слово
        for word in BAD_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", normalized, re.IGNORECASE):
                logger.warning(f"Profanity detected: {word}")
                return False
                
        return True

    def _check_pii(self, response: str) -> bool:
        """Check for PII leakage."""
        pii_patterns = [
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Email
            r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",  # Card
            r"\b\d{10,}\b",  # Phone numbers (10+ digits)
            r"\b\d{3}-\d{3}-\d{2}-\d{2}\b",  # Russian SSN (СНИЛС)
        ]
        for pattern in pii_patterns:
            if re.search(pattern, response):
                logger.warning(f"PII detected by pattern: {pattern}")
                return False
        return True

    def _check_hallucination(self, response: str, context: dict) -> bool:
        """Check for potential hallucinations."""
        # Check for unauthorized discount promises
        discount_patterns = [
            r"скидк[аеуои]\s+(\d{2,3})\s*%",
            r"discount\s+of\s+(\d{2,3})\s*percent",
            r"скидк[аеуои]\s+(\d{2,3})\s*процент",
        ]
        for pattern in discount_patterns:
            match = re.search(pattern, response.lower())
            if match:
                discount_value = int(match.group(1))
                if discount_value > 30:  # Max allowed discount
                    logger.warning(f"Hallucination detected: unauthorized discount {discount_value}%")
                    return False
        return True


# ============================================================
# GLOBAL INSTANCE & CONVENIENCE FUNCTIONS
# ============================================================

# Global validator instance
validator = ResponseValidator()


async def initialize_guardrails():
    """
    Initialize guardrails (call at app startup).
    Pre-loads ML models for toxicity detection.
    """
    logger.info("Initializing guardrails...")
    await ToxicityDetector.initialize()
    if ToxicityDetector.is_ready():
        logger.info("Guardrails initialized with ML toxicity detection")
    else:
        logger.warning("Guardrails initialized with regex fallback only")


async def validate_response(
    response: str,
    context: dict | None = None,
) -> ValidationResult:
    """Convenience function for response validation."""
    return await validator.validate(response, context)