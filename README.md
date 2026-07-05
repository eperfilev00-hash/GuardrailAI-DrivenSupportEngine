<div align="center">

**Support Engine — Multi-Agent AI Customer Support System**

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136.1-green.svg?logo=fastapi)](https://fastapi.tiangolo.com)
<img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License">
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-blue.svg?logo=postgresql)](https://postgresql.org)
[![Docker](https://img.shields.io/badge/Docker-ready-brightgreen.svg?logo=docker)](https://docker.com)

[🚀 Быстрый старт](#-быстрый-старт) •
[🏗 Архитектура](#-архитектура) •
[🛡️ Guardrails](#️-слой-безопасности-guardrails) •
[🛠️ RAG](#-rag-pipeline-retrieval-augmented-generation) •
</div>

---

# 🎯 О проекте
Support Engine — Multi-Agent AI Customer Support System — Асинхронная система интеллектуальной поддержки клиентов на базе Multi-Agent архитектуры с Guardrails, RAG (Retrieval-Augmented Generation) и потоковой генерацией ответов.

# 📦 Стек технологий
| Компонент       | Технология                                                  |
| --------------- | ----------------------------------------------------------- |
| Фреймворк       | FastAPI + Uvicorn                                           |
| База данных     | PostgreSQL 16 + asyncpg + pgvector                          |
| Кэш             | Rate Limiting Redis 7                                       |
| ORM             | SQLAlchemy 2.0 + Alembic                                    |
| LLM             | Anthropic Claude / OpenAI (через унифицированный интерфейс) |
| Embeddings      | sentence-transformers (all-MiniLM-L6-v2)                    |
| Guardrails      | Detoxify (ML-модель) + regex fallback                       |
| Логирование     | structlog + PII masking                                     |
| Контейнеризация | Docker + docker-compose                                     |

---

# 📂 Структура проекта
```
app/
├── main.py                  # Точка входа, lifespan
├── config.py                # Настройки (.env)
├── api/                     # REST + WebSocket роуты
│   ├── routes.py            # API endpoints
│   ├── websocket.py         # WebSocket handler
│   ├── analytics.py         # Аналитика
│   └── registr/auth.py      # Аутентификация
├── agents/                  # Multi-Agent система
│   ├── base.py              # Базовый класс агента
│   ├── router.py            # RouterAgent + маршрутизация
│   └── agents.py            # Специализированные агенты
├── llm/                     # Абстракция над LLM
│   ├── base.py              # Базовый LLM интерфейс
│   ├── factory.py           # Фабрика провайдеров
│   ├── openai.py            # OpenAI provider
│   └── anthropic.py         # Anthropic provider
├── rag/                     # RAG pipeline
│   └── retriever.py         # Векторный поиск
├── guardrails/              # Система валидации
│   └── validator.py         # ResponseValidator + ToxicityDetector
├── services/                # Бизнес-логика
│   ├── orders.py            # Сервис заказов
│   └── session_store.py     # Хранилище сессий
├── db/                      # Модель данных
│   ├── models.py            # SQLAlchemy модели
│   └── database.py          # Подключение к БД
├── middleware/              # Middleware
│   └── rate_limiter.py      # Rate limiting (Redis)
├── logging/                 # Логирование
│   └── pii_processor.py     # PII masking для логов
├── tasks/                   # Фоновые задачи
│   └── worker.py
└── cache/                   # Кэш-слой
    └── redis_client.py
```
 
# 🏗 Архитектура
![Схема](./content/mermaid-diagram.png)
---

# 🚀 Быстрый старт
## 📋 Предварительные требования

```bash
- Python 3.11+
- PostgreSQL 16+
- Redis 7+
- Docker и Docker Compose (опционально)
```
### ⚙️ Конфигурация

Создайте файл `.env` в корне проекта:

```env
DATABASE_URL=postgresql+asyncpg://myuser:mypassword@localhost:5432/mydb
REDIS_URL=redis://localhost:6379/0
ANTHROPIC_API_KEY=your_api_key_here
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
RATE_LIMIT_PER_MINUTE=30
RATE_LIMIT_PER_HOUR=200
RATE_LIMIT_PER_DAY=2000
```
# 🐳 Запуск через Docker Compose
```bash
# Сборка и запуск
docker compose up --build

# Или в фоновом режиме
docker compose up --build -d
# Управление контейнерами

docker compose down          # Остановить
docker compose down -v       # Остановить и удалить тома БД
docker compose logs -f app
```
# 🖥 Ручной запуск (локально)
 - создаём БД
```sql
CREATE USER myuser WITH PASSWORD 'mypassword';
CREATE DATABASE mydb OWNER myuser;
```
### Устанавка зависимостей 
```bash
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
# venv\Scripts\activate         # Windows

pip install -r requirements.txt
```
### Применение миграций
```bash
alembic -c app/alembic.ini upgrade head
```
### Запуск приложения 
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```


# 🤖 Агенты

| Агент          | Назначение                          | Ключевые                        |
| -------------- | ----------------------------------- | ------------------------------- |
| GeneralAgent   | Общие вопросы, приветствия, фоллбэк | —                               |
| OrderAgent     | Статус заказов, трекинг, доставка   | заказ, доставка, трек, статус   |
| ReturnAgent    | Возвраты и возвраты средств         | возврат, вернуть, refund        |
| TechnicalAgent | Баги, ошибки, технические проблемы  | ошибка, баг, не работает, crash |

## Жизненный цикл и обязанности Агента

Каждый агент в системе является изолированным функциональным модулем и выполняет строго определенный цикл обработки сообщения:

* **🔍 Оценка приоритета (`can_handle`)**
    Агент анализирует входящий запрос и возвращает скоринг/вероятность того, насколько данный запрос относится к его зоне ответственности. Это позволяет `RouterAgent` эффективно распределять задачи.
* **🧠 Генерация ответа (LLM + Context)**
    Формирование финального ответа с использованием языковых моделей. В зависимости от специфики агента, в промпт динамически подмешивается контекст:
    * Из базы данных (история сессии, профиль пользователя).
    * Из **RAG Pipeline** (векторный поиск по базе знаний).
* **📦 Стандартизация выхода (`AgentResponse`)**
    Агент никогда не возвращает «сырой» текст. Результатом работы всегда является строго валидированный объект `AgentResponse`, содержащий сам ответ и метаданные для логирования и аналитики.
---
# 🛡️ Слой Безопасности (Guardrails)

Архитектурный компонент контроля качества, который осуществляет сквозную валидацию и фильтрацию сгенерированных ИИ ответов **строго до** их отправки конечному пользователю. 

---

## 🧭 Компоненты фильтрации и защиты

Система безопасности состоит из четырех независимых защитных барьеров (Guardrails Pipelines):

* **☣️ Toxicity Detection (Детекция токсичности)**
  Анализ текста на агрессию, оскорбления и неприемлемый контент.
  * **Основной стек:** Мультиязычная ML-модель `Detoxify` (поддержка *ru / en / es*).
  * **Отказоустойчивость:** Автоматический fallback на легковесные регулярные выражения (regex) при сбоях модели.
  * **Оптимизация:** Результаты проверки кэшируются в **Redis (TTL: 1 час)** для предотвращения повторного сканирования идентичных фраз и снижения нагрузки на CPU/GPU.

* **🤬 Profanity Filter (Фильтр нецензурной лексики)**
  Прямая блокировка обсценной лексики и мата. Использует оптимизированные и регулярно обновляемые списки стоп-слов для русского и английского языков.

* **🔒 PII Leak Prevention (Защита персональных данных)**
  Сканирует ответ на наличие конфиденциальной информации. Блокирует или маскирует (маскирование символами `***`) следующие сущности:
  * Email-адреса и номера телефонов.
  * Номера банковских карт (проверка по алгоритму Луна).
  * Государственные идентификаторы (СНИЛС, ИНН, Паспорта РФ).

* **📉 Hallucination Guard (Контроль галлюцинаций)**
  Бизнес-валидатор, защищающий компанию от финансовых и репутационных рисков. 
  * Автоматически блокирует ответы, в которых модель несанкционированно обещает клиенту **скидки более 30%** или упоминает несуществующие акции.

---

### 🔀 Логика обработки инцидентов (Fallback-стратегия)

При срабатывании любого из триггеров безопасности стандартный `SupportResponse` блокируется, и система переключается на безопасный сценарий:

```json
{
  "status": "rejected",
  "reason": "PII_LEAK_DETECTED",
  "fallback_response": {
    "text": "Извините, я не могу отправить это сообщение, так как оно содержит конфиденциальные данные. Сформулируйте запрос иначе.",
    "action": "clear_input"
  }
}
```
---
# 🔍 RAG Pipeline (Retrieval-Augmented Generation)

Компонент обогащения контекста, который извлекает релевантные знания из внутренней базы данных компании и подмешивает их в промпт для LLM. Это позволяет модели предоставлять точные ответы без галлюцинаций, опираясь на актуальную документацию.

---

## 🛠️ Архитектура и Технический Стек

Процесс векторного поиска и работы со знаниями построен на следующих технологиях:

* **🧠 Генерация эмбеддингов (`all-MiniLM-L6-v2`)**
  Для превращения текстовых документов и входящих запросов пользователей в векторные представления (векторы фиксированной размерности) используется легковесная и быстрая мультиязычная модель из семейства `sentence-transformers`.

* **🗄️ Векторное хранилище (PostgreSQL + `pgvector`)**
  Вместо выделенной векторной DB используется проверенная временем реляционная база **PostgreSQL** с официальным расширением **`pgvector`**. Это позволяет хранить метаданные документов и их векторы в рамках одной СУБД, обеспечивая ACID-транзакции.

* **📐 Метрика близости (Cosine Similarity)**
  Поиск релевантных кусков текста (чанков) выполняется через расчет косинусного расстояния между вектором запроса пользователя ($V_q$) и векторами документов ($V_d$) в базе данных:
  $$\text{Similarity} = \frac{V_q \cdot V_d}{\|V_q\| \|V_d\|}$$
  Чем ближе значение к $1.0$, тем более похож документ по смыслу на вопрос пользователя.

* **⚙️ Порог отсечения (`score_threshold`)**
  Система использует динамически настраиваемый параметр порога релевантности. Если косинусное сходство документа ниже заданного `score_threshold` (например, $< 0.7$), документ отбрасывается и не передается в LLM, чтобы не засорять контекст лишней информацией.

---

### 💻 Пример SQL-запроса для поиска контекста

Благодаря расширению `pgvector`, поиск похожих документов по косинусному расстоянию (оператор `<=>`) выглядит как классический SQL-запрос:

```sql
SELECT id, content, 1 - (embedding <=> :request_embedding) AS similarity_score
FROM knowledge_base
WHERE 1 - (embedding <=> :request_embedding) >= :score_threshold
ORDER BY similarity_score DESC
LIMIT :top_k;
```
# 📝 Лицензия
Проект разработан для демонстрации навыков backend-разработки.
MIT License — используйте свободно.           
---
Сделано с любовью к кофе☕ и коду