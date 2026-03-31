"""
Обработчики удаления ключей и синхронизации удалённых ключей (админка).
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery
import logging
import json

from bot.utils.text import safe_edit_or_send
from bot.keyboards.admin_users import (
    key_delete_confirm_kb,
    sync_deleted_menu_kb,
    sync_deleted_panel_confirm_kb,
    sync_deleted_db_confirm_kb,
    user_view_kb,
    users_menu_kb
)
from database.requests import (
    get_vpn_key_by_id,
    delete_vpn_key,
    get_user_vpn_keys,
    get_active_servers,
    get_users_stats
)
from bot.services.vpn_api import get_client_from_server_data

logger = logging.getLogger(__name__)
router = Router()


# ──────────────────────────── Удаление одного ключа ────────────────────────────

@router.callback_query(F.data.startswith('admin_key_delete_ask:'))
async def on_key_delete_ask(callback: CallbackQuery):
    """Подтверждение удаления отдельного ключа."""
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)

    if not key:
        await callback.answer("❌ Ключ не найден.", show_alert=True)
        return

    user_telegram_id = key.get('telegram_id', 0)

    await safe_edit_or_send(
        callback.message,
        f"⚠️ *Внимание!*\n\nВы действительно хотите удалить ключ `#{key_id}`?\n"
        f"Он будет безвозвратно удален из БД и навсегда удален с VPN-сервера.",
        reply_markup=key_delete_confirm_kb(key_id, user_telegram_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin_key_delete_confirm:'))
async def on_key_delete_confirm(callback: CallbackQuery):
    """Удаление ключа: сначала из БД, потом с панели."""
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)

    if not key:
        await callback.answer("❌ Ключ уже был удален.", show_alert=True)
        return

    user_telegram_id = key.get('telegram_id', 0)

    # 1. Сначала удаляем из БД — это главный источник правды
    delete_vpn_key(key_id)
    logger.info(f"Ключ #{key_id} удалён из БД (админ)")

    # 2. Затем пытаемся удалить с панели, если ключ был привязан
    panel_deleted = False
    if (key.get('server_active') and key.get('panel_inbound_id')
            and key.get('client_uuid') and key.get('host')):
        try:
            server_data = _build_server_data(key)
            client = get_client_from_server_data(server_data)
            await client.delete_client(key['panel_inbound_id'], key['client_uuid'])
            panel_deleted = True
            logger.info(f"Ключ #{key_id} удалён с панели {key.get('server_name')}")
        except Exception as e:
            logger.warning(f"Не удалось удалить ключ #{key_id} с панели: {e}")

    status = "✅ Ключ удалён из БД и с панели" if panel_deleted else "✅ Ключ удалён из БД"
    await callback.answer(status, show_alert=True)

    # Возвращаемся в профиль пользователя
    if user_telegram_id:
        from database.requests import get_user_by_telegram_id
        user = get_user_by_telegram_id(user_telegram_id)
        if user:
            keys = get_user_vpn_keys(user['id'])
            await safe_edit_or_send(
                callback.message,
                f"👤 Профиль пользователя: *{user.get('username', 'Нет')}* (ID: `{user['telegram_id']}`)",
                reply_markup=user_view_kb(
                    user['telegram_id'],
                    keys,
                    bool(user.get('is_banned', 0)),
                    user.get('balance_cents', 0),
                    user.get('referral_coefficient', 1.0)
                ),
                parse_mode="Markdown"
            )
            return

    # Фолбэк — меню пользователей
    stats = get_users_stats()
    await safe_edit_or_send(
        callback.message,
        "👥 *Управление пользователями*",
        reply_markup=users_menu_kb(stats),
        parse_mode="Markdown"
    )


# ──────────────────── Синхронизация удалённых ключей ────────────────────────

@router.callback_query(F.data == 'admin_sync_deleted_menu')
async def on_sync_deleted_menu(callback: CallbackQuery):
    """Подменю синхронизации удаленных ключей."""
    await safe_edit_or_send(
        callback.message,
        "🗑️ *Синхронизация удалённых ключей*\n\n"
        "Выберите действие:\n"
        "1. *Очистить панель*: удаляет с VPN-серверов ключи, которых *нет в нашей базе*.\n"
        "2. *Очистить базу*: удаляет из нашей БД ключи, которых *нет на VPN-серверах*.\n\n"
        "⚠️ *Внимание: обе операции необратимы!*",
        reply_markup=sync_deleted_menu_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


# ──────────────── Очистка панели (удалить сирот с серверов) ─────────────────

@router.callback_query(F.data == 'admin_sync_deleted_panel_ask')
async def on_sync_deleted_panel_ask(callback: CallbackQuery):
    """Спрашиваем подтверждение на очистку панели."""
    await safe_edit_or_send(
        callback.message,
        "🧹 *Очистка панели*\n\n"
        "Вы собираетесь удалить с VPN-серверов ключи (почты которых начинаются на `user_`), "
        "которых нет в базе данных этого бота.\n\n"
        "Вы уверены?",
        reply_markup=sync_deleted_panel_confirm_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == 'admin_sync_deleted_panel_confirm')
async def on_sync_deleted_panel_confirm(callback: CallbackQuery):
    """Удаление 'осиротевших' ключей с VPN-серверов."""
    await safe_edit_or_send(
        callback.message,
        "⏳ *Очистка панели: собираю данные...*\n\nПожалуйста, подождите.",
        parse_mode="Markdown"
    )

    servers = get_active_servers()

    # Собираем ВСЕ panel_email из БД (включая ключи без server_id)
    from database.connection import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT LOWER(panel_email) as email FROM vpn_keys WHERE panel_email IS NOT NULL"
        ).fetchall()
    db_emails_all = {r['email'] for r in rows}

    deleted_count = 0
    errors_count = 0

    for server in servers:
        try:
            client = get_client_from_server_data(server)
            inbounds = await client.get_inbounds()

            for inbound in inbounds:
                inbound_id = inbound['id']
                settings = json.loads(inbound.get('settings', '{}'))
                clients = settings.get('clients', [])

                for cl in clients:
                    cl_email = cl.get('email', '')
                    # Только ключи нашего бота (user_*), которых нет в БД
                    if cl_email.lower().startswith('user_') and cl_email.lower() not in db_emails_all:
                        try:
                            client_uuid = cl.get('id') or cl.get('password')
                            await client.delete_client(inbound_id, client_uuid)
                            deleted_count += 1
                            logger.info(f"Очистка панели: удалён сирота {cl_email} с {server['name']}")
                        except Exception as e:
                            logger.error(f"Очистка панели: ошибка удаления {cl_email}: {e}")
                            errors_count += 1

        except Exception as e:
            logger.error(f"Очистка панели: ошибка связи с {server['name']}: {e}")
            errors_count += 1

    await safe_edit_or_send(
        callback.message,
        f"✅ *Очистка панели завершена*\n\n"
        f"🗑 Удалено ключей-сирот: *{deleted_count}*\n"
        f"❌ Ошибок: *{errors_count}*",
        reply_markup=sync_deleted_menu_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


# ───────────── Очистка базы (удалить из БД ключи, которых нет на панели) ──────────

@router.callback_query(F.data == 'admin_sync_deleted_db_ask')
async def on_sync_deleted_db_ask(callback: CallbackQuery):
    """Спрашиваем подтверждение на очистку БД."""
    await safe_edit_or_send(
        callback.message,
        "🗑️ *Очистка базы данных*\n\n"
        "Вы собираетесь удалить из БД бота ключи, которых *уже не существует на их VPN-серверах* "
        "(например, если кто-то удалил их вручную через веб-интерфейс).\n\n"
        "Также будут удалены ключи-сироты: привязанные к несуществующему серверу.\n\n"
        "Вы уверены?",
        reply_markup=sync_deleted_db_confirm_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == 'admin_sync_deleted_db_confirm')
async def on_sync_deleted_db_confirm(callback: CallbackQuery):
    """Удаление ключей из БД, которых нет на панели."""
    await safe_edit_or_send(
        callback.message,
        "⏳ *Очистка базы данных: проверяю серверы...*\n\nПожалуйста, подождите.",
        parse_mode="Markdown"
    )

    servers = get_active_servers()
    active_server_ids = {s['id'] for s in servers}

    # Берём ВСЕ настроенные ключи из БД (с panel_email)
    from database.connection import get_db
    with get_db() as conn:
        rows = conn.execute("""
            SELECT vk.id, vk.panel_email, vk.server_id
            FROM vpn_keys vk
            WHERE vk.panel_email IS NOT NULL
        """).fetchall()
    all_configured_keys = [dict(r) for r in rows]

    deleted_count = 0
    orphan_count = 0
    errors_count = 0
    ok_count = 0

    # 1. Сначала удаляем ключи-сироты (server_id = NULL или сервер неактивен/удалён)
    for key in all_configured_keys:
        sid = key.get('server_id')
        if sid is None or sid not in active_server_ids:
            try:
                delete_vpn_key(key['id'])
                orphan_count += 1
                logger.info(f"Очистка БД: удалён ключ-сирота ID {key['id']} (server_id={sid})")
            except Exception as e:
                logger.error(f"Очистка БД: ошибка удаления ключа ID {key['id']}: {e}")
                errors_count += 1

    # 2. Проверяем ключи на активных серверах
    for server in servers:
        sid = server['id']
        keys_on_server = [k for k in all_configured_keys
                          if k.get('server_id') == sid]
        if not keys_on_server:
            continue

        try:
            client = get_client_from_server_data(server)
            inbounds = await client.get_inbounds()

            # Собираем email всех клиентов на сервере
            panel_emails = set()
            for inbound in inbounds:
                settings = json.loads(inbound.get('settings', '{}'))
                for cl in settings.get('clients', []):
                    panel_emails.add(cl.get('email', '').lower())

            # Сверяем
            for key in keys_on_server:
                key_email = (key.get('panel_email') or '').lower()
                if key_email not in panel_emails:
                    try:
                        delete_vpn_key(key['id'])
                        deleted_count += 1
                        logger.info(f"Очистка БД: удалён ключ ID {key['id']} ({key_email}) — нет на панели")
                    except Exception as e:
                        logger.error(f"Очистка БД: ошибка удаления ключа ID {key['id']}: {e}")
                        errors_count += 1
                else:
                    ok_count += 1

        except Exception as e:
            logger.error(f"Очистка БД: ошибка связи с {server['name']}: {e}")
            errors_count += 1

    await safe_edit_or_send(
        callback.message,
        f"✅ *Очистка базы завершена*\n\n"
        f"🗑 Удалено (нет на панели): *{deleted_count}*\n"
        f"👻 Удалено сирот (нет сервера): *{orphan_count}*\n"
        f"✅ На месте (найдены на серверах): *{ok_count}*\n"
        f"❌ Ошибок: *{errors_count}*",
        reply_markup=sync_deleted_menu_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


# ──────────────────────── Утилиты ────────────────────────

def _build_server_data(key: dict) -> dict:
    """Формирует server_data из данных ключа для get_client_from_server_data."""
    return {
        'id': key.get('server_id'),
        'name': key.get('server_name'),
        'host': key.get('host'),
        'port': key.get('port'),
        'web_base_path': key.get('web_base_path', ''),
        'login': key.get('login'),
        'password': key.get('password'),
        'protocol': key.get('protocol', 'https')
    }
