"""Structlog processor for masking PII (Personally Identifiable Information).

Complies with GDPR and ФЗ-152 requirements by redacting sensitive data before logging.
"""

import re
from typing import Any

from structlog.types import EventDict



# Email addresses
EMAIL_PATTERN = re.compile(
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    re.IGNORECASE
)

# Phone numbers (various formats)
PHONE_PATTERNS = [
    re.compile(r'\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}'),
    re.compile(r'\d{3}[-.\s]?\d{3}[-.\s]?\d{2}[-.\s]?\d{2}'),  # Russian format
    re.compile(r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}'),  # US format
]

# Credit card numbers (13-19 digits with optional separators)
CARD_PATTERN = re.compile(
    r'\b(?:\d{4}[-\s]?){3,4}\d{1,4}\b'
)

# Russian SSN (СНИЛС): XXX-XXX-XXX XX
SNILS_PATTERN = re.compile(r'\b\d{3}-\d{3}-\d{3}\s?\d{2}\b')

# Session tokens / API keys (alphanumeric strings 20+ chars)
TOKEN_PATTERN = re.compile(r'\b[A-Za-z0-9_-]{20,}\b')

# IP addresses
IP_PATTERN = re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')

# КЛЮЧИ ДЛЯ МАСКИРОВКИ 
SENSITIVE_KEYS = {
    'password', 'passwd', 'pwd', 'secret', 'token', 'api_key', 'apikey',
    'access_token', 'refresh_token', 'session_id', 'session_token',
    'email', 'user_email', 'useremail',
    'phone', 'telephone', 'mobile', 'cellphone',
    'address', 'shipping_address', 'billing_address', 'street',
    'card', 'card_number', 'cc_number', 'credit_card',
    'ssn', 'snils', 'passport',
    'ip_address', 'ip', 'client_ip',
    'hashed_password', 'password_hash',
}

# МАСКИРОВКА
MASK_EMAIL = '[EMAIL_REDACTED]'
MASK_PHONE = '[PHONE_REDACTED]'
MASK_CARD = '[CARD_REDACTED]'
MASK_TOKEN = '[TOKEN_REDACTED]'
MASK_IP = '[IP_REDACTED]'
MASK_DEFAULT = '[REDACTED]'


def _mask_value(value: Any, key: str | None = None) -> Any:
    """
    Маскирует чувствительное значение.
    
    Args:
        value: Значение для маскировки
        key: Ключ словаря (для проверки SENSITIVE_KEYS)
        
    Returns:
        Замаскированное значение или оригинал
    """
    if not isinstance(value, str):
        return value
    
    # Проверка по ключу (приоритет)
    if key and key.lower() in SENSITIVE_KEYS:
        return MASK_DEFAULT
    
    # Проверка по паттернам
    if EMAIL_PATTERN.search(value):
        return EMAIL_PATTERN.sub(MASK_EMAIL, value)
    
    for pattern in PHONE_PATTERNS:
        if pattern.search(value):
            return pattern.sub(MASK_PHONE, value)
    
    if CARD_PATTERN.search(value):
        return CARD_PATTERN.sub(MASK_CARD, value)
    
    if SNILS_PATTERN.search(value):
        return SNILS_PATTERN.sub(MASK_DEFAULT, value)
    
    if TOKEN_PATTERN.search(value):
        if len(value) >= 32:
            return TOKEN_PATTERN.sub(MASK_TOKEN, value)
    
    if IP_PATTERN.search(value):
        return IP_PATTERN.sub(MASK_IP, value)
    
    return value


def _process_dict(data: dict[str, Any], depth: int = 0, max_depth: int = 5) -> dict[str, Any]:
    """
    Рекурсивная обработка словаря для маскировки PII.
    """
    if depth > max_depth:
        return data
    
    result = {}
    for key, value in data.items():
        key_lower = key.lower()
        
        if isinstance(value, dict):
            result[key] = _process_dict(value, depth + 1, max_depth)
        elif isinstance(value, (list, tuple)):
            result[key] = [
                _process_dict(item, depth + 1, max_depth) if isinstance(item, dict)
                else _mask_value(item, key_lower)
                for item in value
            ]
        else:
            result[key] = _mask_value(value, key_lower)
    
    return result


# ПРАВИЛЬНАЯ АННОТАЦИЯ ТИПА
def mask_pii_processor(
    logger: Any, 
    method_name: str, 
    event_dict: EventDict
) -> EventDict:
    """
    Structlog processor для маскировки PII перед записью в лог.
    
    Должен быть добавлен в processors ПЕРЕД JSONRenderer.
    
    Example:
        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.add_log_level,
                mask_pii_processor,  # ← Добавить перед JSONRenderer
                structlog.processors.JSONRenderer(),
            ],
        )
    
    Args:
        logger: Logger instance
        method_name: Метод логирования (info, warning, error)
        event_dict: Словарь с данными лога
        
    Returns:
        Обработанный event_dict с замаскированными PII
    """
    # Преобразуем MutableMapping -> dict для обработки
    data = dict(event_dict)
    masked_data = _process_dict(data)
    
    # Возвращаем обратно как EventDict (совместимо)
    event_dict.clear()
    event_dict.update(masked_data)
    return event_dict