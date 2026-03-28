"""
Фасад для работы с API VPN-панелей.
"""
import logging
from typing import Optional, Dict, Any, List
import asyncio

from .panels.base import VPNAPIError, BaseVPNClient
from .panels.xui import XUIClient
from .panels.marzban import MarzbanClient

logger = logging.getLogger(__name__)

_clients: Dict[int, BaseVPNClient] = {}

def get_client_from_server_data(server: Dict[str, Any]) -> BaseVPNClient:
    """
    Создает или возвращает экземпляр клиента для API панели.
    """
    server_id = server['id']
    if server_id in _clients:
        return _clients[server_id]
        
    pass_type = server.get('panel_type', 'xui')
    if pass_type == 'marzban':
        client = MarzbanClient(server)
    else:
        client = XUIClient(server)
        
    _clients[server_id] = client
    return client

def invalidate_client_cache(server_id: int):
    """Инвалидирует сессию клиента."""
    if server_id in _clients:
        client = _clients[server_id]
        import asyncio
        asyncio.create_task(client.close())
        del _clients[server_id]
        logger.debug(f'Кэш клиента {server_id} очищен')

def format_traffic(bytes_count: int) -> str:
    """Форматирует байты в читабельный вид."""
    if bytes_count < 1024:
        return f'{bytes_count} B'
    elif bytes_count < 1024 ** 2:
        return f'{bytes_count / 1024:.1f} KB'
    elif bytes_count < 1024 ** 3:
        return f'{bytes_count / 1024 ** 2:.1f} MB'
    elif bytes_count < 1024 ** 4:
        return f'{bytes_count / 1024 ** 3:.2f} GB'
    else:
        return f'{bytes_count / 1024 ** 4:.2f} TB'

async def close_all_clients():
    """Закрывает все открытые сессии клиентов."""
    for client in list(_clients.values()):
        try:
            await client.close()
        except Exception as e:
            logger.error(f"Ошибка при закрытии клиента: {e}")
    _clients.clear()

async def get_client(server_id: int) -> XUIClient:
    """
    Получает клиент для сервера по ID (из БД).
    
    Args:
        server_id: ID сервера в БД
        
    Returns:
        Экземпляр XUIClient
        
    Raises:
        ValueError: Если сервер не найден
    """
    from database.requests import get_server_by_id
    if server_id in _clients:
        return _clients[server_id]
    server = get_server_by_id(server_id)
    if not server:
        raise ValueError(f'Сервер с ID {server_id} не найден')
    return get_client_from_server_data(server)

async def test_server_connection(server_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет подключение к серверу.
    
    Args:
        server_data: Словарь с данными сервера
        
    Returns:
        Словарь с результатом:
        - success: True если подключение успешно
        - message: Сообщение о результате
        - stats: Статистика (если успешно)
    """
    client = XUIClient(server_data)
    try:
        await client.login()
        stats = await client.get_stats()
        return {'success': True, 'message': 'Подключение успешно!', 'stats': stats}
    except VPNAPIError as e:
        return {'success': False, 'message': f'Ошибка: {e}', 'stats': None}
    finally:
        await client.close()

async def reset_key_traffic_if_active(key_id: int) -> bool:
    """
    Сбрасывает израсходованный трафик ключа в панели 3X-UI,
    если сервер активен.
    
    Args:
        key_id: ID ключа (VPNKey.id)
        
    Returns:
        True при успешном сбросе, иначе False.
    """
    from database.requests import get_vpn_key_by_id
    key = get_vpn_key_by_id(key_id)
    if not key or not key.get('server_active'):
        return False
    server_data = {'id': key.get('server_id'), 'name': key.get('server_name'), 'host': key.get('host'), 'port': key.get('port'), 'web_base_path': key.get('web_base_path'), 'login': key.get('login'), 'password': key.get('password')}
    inbound_id = key.get('panel_inbound_id')
    email = key.get('panel_email')
    if not email:
        if key.get('username'):
            email = f"user_{key['username']}"
        else:
            email = f"user_{key['telegram_id']}"
    try:
        client = get_client_from_server_data(server_data)
        success = await client.reset_client_traffic(inbound_id, email)
        if success:
            logger.info(f'Трафик ключа {key_id} успешно сброшен при продлении.')
        return success
    except Exception as e:
        logger.error(f'Не удалось сбросить трафик ключа {key_id} при продлении: {e}')
        return False

async def extend_key_on_server(key_id: int, days: int) -> bool:
    """
    Продлевает срок действия ключа в панели 3X-UI, если сервер активен.
    
    Args:
        key_id: ID ключа (VPNKey.id)
        days: Количество дней для продления
        
    Returns:
        True при успешном продлении, иначе False.
    """
    from database.requests import get_vpn_key_by_id
    key = get_vpn_key_by_id(key_id)
    if not key or not key.get('server_active'):
        return False
    server_data = {'id': key.get('server_id'), 'name': key.get('server_name'), 'host': key.get('host'), 'port': key.get('port'), 'web_base_path': key.get('web_base_path'), 'login': key.get('login'), 'password': key.get('password')}
    inbound_id = key.get('panel_inbound_id')
    client_uuid = key.get('client_uuid')
    email = key.get('panel_email')
    if not email:
        email = f"user_{key.get('username') or key.get('telegram_id')}"
    try:
        client = get_client_from_server_data(server_data)
        success = await client.extend_client_expiry(inbound_id, client_uuid, email, days)
        if success:
            logger.info(f'Срок действия ключа {key_id} успешно продлен на сервере на {days} дней.')
        return success
    except Exception as e:
        logger.error(f'Не удалось продлить срок действия ключа {key_id} на сервере: {e}')
        return False


__all__ = ["VPNAPIError", "get_client_from_server_data", "invalidate_client_cache", "format_traffic", "close_all_clients", "get_client", "test_server_connection", "reset_key_traffic_if_active", "extend_key_on_server"]
