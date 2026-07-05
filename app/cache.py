import asyncio

# это независимое центральное хранилище кэша
_dashboard_cache = {}
_cache_lock = asyncio.Lock()