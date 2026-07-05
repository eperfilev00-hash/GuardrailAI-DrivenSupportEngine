"""JSON validation utilities for security."""

import logging
from typing import Any, Union

logger = logging.getLogger(__name__)


MAX_JSON_DEPTH = 5  # Максимальная глубина вложенности
MAX_JSON_KEYS = 100  # Максимум ключей на объект
MAX_STRING_LENGTH = 10000  # Максимум символов в строке
MAX_LIST_LENGTH = 50  # Максимум элементов в списке


class JSONDepthError(ValueError):
    """Превышена максимальная глубина вложенности JSON."""
    pass


class JSONSizeError(ValueError):
    """Превышен максимальный размер JSON."""
    pass


def validate_json_depth(value: Any, current_depth: int = 0, key_count: dict | None = None) -> Any:
    """
    Рекурсивная валидация глубины и размера JSON-структуры.
    
    Защищает от:
    - Hash Collision DoS (глубокая вложенность)
    - Чрезмерного размера данных
    
    Args:
        value: Проверяемое значение (dict, list, или примитив)
        current_depth: Текущая глубина рекурсии
        key_count: Счётчик ключей для отслеживания общего количества
        
    Returns:
        Валидированное значение (если прошло проверку)
        
    Raises:
        JSONDepthError: Если глубина превышает MAX_JSON_DEPTH
        JSONSizeError: Если превышено количество ключей/элементов
    """
    if key_count is None:
        key_count = {"total": 0}
    
    # Проверка глубины
    if current_depth > MAX_JSON_DEPTH:
        raise JSONDepthError(
            f"JSON depth {current_depth} exceeds maximum allowed {MAX_JSON_DEPTH}"
        )
    
    if isinstance(value, dict):
        # Проверка количества ключей
        key_count["total"] += len(value)
        if key_count["total"] > MAX_JSON_KEYS:
            raise JSONSizeError(
                f"Total JSON keys {key_count['total']} exceeds maximum {MAX_JSON_KEYS}"
            )
        
        # Рекурсивная валидация значений
        validated = {}
        for key, val in value.items():
            if not isinstance(key, str):
                raise JSONSizeError(f"JSON keys must be strings, got {type(key)}")
            
            # Проверка длины ключа
            if len(key) > 100:
                raise JSONSizeError(f"JSON key length {len(key)} exceeds maximum 100")
            
            validated[key] = validate_json_depth(val, current_depth + 1, key_count)
        
        return validated
    
    elif isinstance(value, list):
        # Проверка размера списка
        if len(value) > MAX_LIST_LENGTH:
            raise JSONSizeError(
                f"List length {len(value)} exceeds maximum {MAX_LIST_LENGTH}"
            )
        
        # Рекурсивная валидация элементов
        return [
            validate_json_depth(item, current_depth + 1, key_count)
            for item in value
        ]
    
    elif isinstance(value, str):
        # Проверка длины строки
        if len(value) > MAX_STRING_LENGTH:
            raise JSONSizeError(
                f"String length {len(value)} exceeds maximum {MAX_STRING_LENGTH}"
            )
        return value
    
    # Примитивы (int, float, bool, None) возвращаем как есть
    return value


def safe_json_loads(data: Union[str, dict, list], max_depth: int = MAX_JSON_DEPTH) -> Any:
    """
    Безопасная загрузка JSON с валидацией глубины.
    
    Args:
        data: JSON-строка или уже распарсенный dict/list
        max_depth: Максимальная глубина вложенности
        
    Returns:
        Валидированная структура данных
        
    Raises:
        JSONDepthError: Если глубина превышает лимит
        JSONSizeError: Если размер превышает лимит
    """
    import json
    
    # Если строка — парсим
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
    else:
        parsed = data
    
    # Валидируем структуру
    return validate_json_depth(parsed, current_depth=0, key_count={"total": 0})