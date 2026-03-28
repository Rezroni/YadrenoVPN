import logging
import uuid
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestUsers, UsersShared, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from config import ADMIN_IDS
from database.requests import get_users_stats, get_all_users_paginated, get_user_by_telegram_id, toggle_user_ban, get_user_vpn_keys, get_user_payments_stats, get_vpn_key_by_id, extend_vpn_key, create_vpn_key_admin, get_active_servers, get_all_tariffs, get_user_balance, get_user_referral_coefficient, add_to_balance, deduct_from_balance, set_user_referral_coefficient
from bot.utils.admin import is_admin
from bot.utils.text import escape_md
from bot.states.admin_states import AdminStates
from bot.keyboards.admin import users_menu_kb, users_list_kb, user_view_kb, user_ban_confirm_kb, key_view_kb, add_key_server_kb, add_key_inbound_kb, add_key_step_kb, add_key_confirm_kb, users_input_cancel_kb, key_action_cancel_kb, back_and_home_kb, home_only_kb
from bot.services.vpn_api import get_client_from_server_data, VPNAPIError, format_traffic

logger = logging.getLogger(__name__)
router = Router()
USERS_PER_PAGE = 20

def generate_unique_email(user: dict) -> str:
    """
    Генерирует уникальный email для панели 3X-UI.
    Формат: user_{username/id}_{random_suffix}
    """
    base = f"user_{user['username']}" if user.get('username') else f"user_{user['telegram_id']}"
    suffix = uuid.uuid4().hex[:5]
    return f'{base}_{suffix}'

@router.callback_query(F.data.startswith('admin_key_view:'))
async def show_key_view(callback: CallbackQuery, state: FSMContext):
    """Показывает экран управления ключом."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)
    if not key:
        await callback.answer('Ключ не найден', show_alert=True)
        return
    await state.set_state(AdminStates.key_view)
    await state.update_data(current_key_id=key_id)
    if key.get('custom_name'):
        key_name = key['custom_name']
    else:
        uuid = key.get('client_uuid') or ''
        if len(uuid) >= 8:
            key_name = f'{uuid[:4]}...{uuid[-4:]}'
        else:
            key_name = uuid or f'Ключ #{key_id}'
    server_name = key.get('server_name', 'Неизвестный сервер')
    tariff_name = key.get('tariff_name', 'Неизвестный тариф')
    expires_at = key.get('expires_at', '?')
    created_at = key.get('created_at', '?')
    text = f'🔑 *{key_name}*\n\n🖥️ Сервер: {server_name}\n📋 Тариф: {tariff_name}\n📅 Создан: {created_at}\n⏰ Истекает: {expires_at}\n'
    from database.requests import is_key_active, is_traffic_exhausted
    if not is_key_active(key):
        if is_traffic_exhausted(key):
            text += '\n❌ *Трафик исчерпан*\n'
        else:
            text += '\n⏳ *Срок действия истёк*\n'
    traffic_used = key.get('traffic_used', 0) or 0
    traffic_limit = key.get('traffic_limit', 0) or 0
    if traffic_limit > 0:
        remaining = max(0, traffic_limit - traffic_used)
        text += f'\n📊 *Трафик:*\n  ✅ Использовано: {format_traffic(traffic_used)}\n  🎯 Лимит: {format_traffic(traffic_limit)}\n  💾 Остаток: {format_traffic(remaining)}\n'
    else:
        text += f'\n📊 *Трафик:*\n  ✅ Использовано: {format_traffic(traffic_used)}\n  ∞ Без лимита\n'
    from database.requests import get_key_payments_history
    payments_history = get_key_payments_history(key_id)
    if payments_history:
        text += '\n💳 *История платежей:*\n'
        for p in payments_history:
            dt = p['paid_at']
            amount = ''
            if p['payment_type'] == 'crypto':
                usd = p['amount_cents'] / 100
                usd_str = f'{usd:g}'.replace('.', ',')
                amount = f'${usd_str}'
            elif p['payment_type'] == 'stars':
                amount = f"{p['amount_stars']} ⭐"
            elif p.get('payment_type') == 'cards':
                rub = p.get('price_rub') or 0
                rub_str = f'{rub:g}'.replace('.', ',')
                amount = f'{rub_str} ₽'
            else:
                amount = '?'
            tariff_safe = escape_md(p['tariff_name'] or 'Неизвестно')
            text += f'• `{dt}`: {amount} — {tariff_safe}\n'
    else:
        text += '\n💳 *История платежей:* _пусто_\n'
    user_telegram_id = key.get('telegram_id')
    await callback.message.edit_text(text, reply_markup=key_view_kb(key_id, user_telegram_id), parse_mode='Markdown')
    await callback.answer()

@router.callback_query(F.data.startswith('admin_key_extend:'))
async def start_key_extend(callback: CallbackQuery, state: FSMContext):
    """Начало продления ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    await state.set_state(AdminStates.key_extend_days)
    await state.update_data(current_key_id=key_id)
    await callback.message.edit_text('📅 *Продление ключа*\n\nВведите количество дней для продления:', reply_markup=key_action_cancel_kb(key_id, 0), parse_mode='Markdown')
    await callback.answer()

@router.message(AdminStates.key_extend_days, F.text)
async def process_key_extend(message: Message, state: FSMContext):
    """Обработка ввода дней для продления."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain')
    if not text.isdigit() or int(text) < 1 or int(text) > 99999:
        await message.answer('❌ Введите число от 1 до 99999', parse_mode='Markdown')
        return
    days = int(text)
    data = await state.get_data()
    key_id = data.get('current_key_id')
    success = extend_vpn_key(key_id, days)
    if success:
        await message.answer(f'✅ Ключ продлён на {days} дней!')
        from bot.services.vpn_api import reset_key_traffic_if_active, extend_key_on_server
        await reset_key_traffic_if_active(key_id)
        await extend_key_on_server(key_id, days)
        key = get_vpn_key_by_id(key_id)
        if key:
            await state.set_state(AdminStates.key_view)
    else:
        await message.answer('❌ Ошибка продления ключа')

@router.callback_query(F.data.startswith('admin_key_reset_traffic:'))
async def reset_key_traffic(callback: CallbackQuery, state: FSMContext):
    """Сброс трафика ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)
    if not key:
        await callback.answer('Ключ не найден', show_alert=True)
        return
    if not key.get('server_active'):
        await callback.answer('❌ Сервер неактивен', show_alert=True)
        return
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
        await client.reset_client_traffic(inbound_id, email)
        await callback.answer('✅ Трафик успешно сброшен!', show_alert=True)
    except VPNAPIError as e:
        logger.error(f'Ошибка сброса трафика: {e}')
        await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
    except Exception as e:
        logger.error(f'Неожиданная ошибка при сбросе трафика: {e}')
        await callback.answer('❌ Ошибка при сбросе трафика', show_alert=True)

@router.callback_query(F.data.startswith('admin_key_change_traffic:'))
async def start_change_traffic_limit(callback: CallbackQuery, state: FSMContext):
    """Начало изменения лимита трафика."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)
    if not key:
        await callback.answer('Ключ не найден', show_alert=True)
        return
    if not key.get('server_active'):
        await callback.answer('❌ Сервер неактивен', show_alert=True)
        return
    await state.set_state(AdminStates.key_change_traffic)
    await state.update_data(current_key_id=key_id)
    user_telegram_id = key.get('telegram_id')
    await state.update_data(current_user_telegram_id=user_telegram_id)
    await callback.message.edit_text('📊 *Изменение лимита трафика*\n\nВведите новый лимит в ГБ (0 = без лимита):', reply_markup=key_action_cancel_kb(key_id, user_telegram_id), parse_mode='Markdown')
    await callback.answer()

@router.message(AdminStates.key_change_traffic, F.text)
async def process_change_traffic_limit(message: Message, state: FSMContext):
    """Обработка ввода нового лимита трафика."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain')
    if not text.isdigit():
        await message.answer('❌ Введите число (0 = без лимита)')
        return
    traffic_gb = int(text)
    data = await state.get_data()
    key_id = data.get('current_key_id')
    key = get_vpn_key_by_id(key_id)
    if not key:
        await message.answer('❌ Ключ не найден')
        return
    server_data = {'id': key.get('server_id'), 'name': key.get('server_name'), 'host': key.get('host'), 'port': key.get('port'), 'web_base_path': key.get('web_base_path'), 'login': key.get('login'), 'password': key.get('password')}
    inbound_id = key.get('panel_inbound_id')
    client_uuid = key.get('client_uuid')
    email = key.get('panel_email')
    if not email:
        if key.get('username'):
            email = f"user_{key['username']}"
        else:
            email = f"user_{key['telegram_id']}"
    try:
        client = get_client_from_server_data(server_data)
        await client.update_client_traffic_limit(inbound_id=inbound_id, client_uuid=client_uuid, email=email, total_gb=traffic_gb)
        traffic_text = f'{traffic_gb} ГБ' if traffic_gb > 0 else 'без лимита'
        await message.answer(f'✅ Лимит трафика успешно обновлён: {traffic_text}!')
        await state.set_state(AdminStates.key_view)
    except VPNAPIError as e:
        logger.error(f'Ошибка обновления лимита трафика: {e}')
        await message.answer(f'❌ Ошибка: {e}')
    except Exception as e:
        logger.error(f'Неожиданная ошибка при обновлении лимита трафика: {e}')
        await message.answer('❌ Ошибка при обновлении лимита трафика')

@router.callback_query(F.data.startswith('admin_user_add_key:'))
async def start_add_key(callback: CallbackQuery, state: FSMContext):
    """Начало добавления ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    telegram_id = int(callback.data.split(':')[1])
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    servers = get_active_servers()
    if not servers:
        await callback.answer('❌ Нет активных серверов', show_alert=True)
        return
    await state.set_state(AdminStates.add_key_server)
    await state.update_data(add_key_user_id=user['id'], add_key_user_telegram_id=telegram_id)
    await callback.message.edit_text(f'➕ *Добавление ключа для {format_user_display(user)}*\n\nВыберите сервер:', reply_markup=add_key_server_kb(servers), parse_mode='Markdown')
    await callback.answer()

@router.callback_query(F.data.startswith('admin_add_key_server:'))
async def select_add_key_server(callback: CallbackQuery, state: FSMContext):
    """Выбор сервера для нового ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    from database.requests import get_server_by_id
    server_id = int(callback.data.split(':')[1])
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer('Сервер не найден', show_alert=True)
        return
    await state.update_data(add_key_server_id=server_id)
    try:
        client = get_client_from_server_data(server)
        inbounds = await client.get_inbounds()
        if not inbounds:
            await callback.answer('❌ На сервере нет inbound', show_alert=True)
            return
        await state.set_state(AdminStates.add_key_inbound)
        await callback.message.edit_text(f"🖥️ *Сервер:* `{server['name']}`\n\nВыберите протокол (inbound):", reply_markup=add_key_inbound_kb(inbounds), parse_mode='Markdown')
    except VPNAPIError as e:
        await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
    await callback.answer()

@router.callback_query(F.data.startswith('admin_add_key_inbound:'))
async def select_add_key_inbound(callback: CallbackQuery, state: FSMContext):
    """Выбор inbound для нового ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    inbound_id = int(callback.data.split(':')[1])
    await state.update_data(add_key_inbound_id=inbound_id)
    await state.set_state(AdminStates.add_key_traffic)
    await callback.message.edit_text('📊 *Лимит трафика*\n\nВведите лимит в ГБ (0 = без лимита):', reply_markup=add_key_step_kb(2), parse_mode='Markdown')
    await callback.answer()

@router.message(AdminStates.add_key_traffic, F.text)
async def process_add_key_traffic(message: Message, state: FSMContext):
    """Обработка ввода лимита трафика."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain')
    if not text.isdigit():
        await message.answer('❌ Введите число (0 = без лимита)')
        return
    traffic_gb = int(text)
    await state.update_data(add_key_traffic_gb=traffic_gb)
    await state.set_state(AdminStates.add_key_days)
    await message.answer('📅 *Срок действия*\n\nВведите количество дней:', reply_markup=add_key_step_kb(3), parse_mode='Markdown')

@router.message(AdminStates.add_key_days, F.text)
async def process_add_key_days(message: Message, state: FSMContext):
    """Обработка ввода срока действия."""
    if not is_admin(message.from_user.id):
        return
    from bot.utils.text import get_message_text_for_storage
    text = get_message_text_for_storage(message, 'plain')
    if not text.isdigit() or int(text) < 1 or int(text) > 99999:
        await message.answer('❌ Введите число от 1 до 99999')
        return
    days = int(text)
    await state.update_data(add_key_days=days)
    await state.set_state(AdminStates.add_key_confirm)
    data = await state.get_data()
    from database.requests import get_server_by_id
    server = get_server_by_id(data['add_key_server_id'])
    traffic_text = f"{data.get('add_key_traffic_gb', 0)} ГБ" if data.get('add_key_traffic_gb', 0) > 0 else 'без лимита'
    await message.answer(f"✅ *Подтверждение создания ключа*\n\n🖥️ Сервер: {(server['name'] if server else '?')}\n📊 Трафик: {traffic_text}\n📅 Срок: {days} дней\n", reply_markup=add_key_confirm_kb(), parse_mode='Markdown')

@router.callback_query(F.data == 'admin_add_key_confirm')
async def confirm_add_key(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение и создание ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    data = await state.get_data()
    user_id = data.get('add_key_user_id')
    user_telegram_id = data.get('add_key_user_telegram_id')
    server_id = data.get('add_key_server_id')
    inbound_id = data.get('add_key_inbound_id')
    traffic_gb = data.get('add_key_traffic_gb', 0)
    days = data.get('add_key_days', 30)
    from database.requests import get_server_by_id
    server = get_server_by_id(server_id)
    if not server:
        await callback.answer('Сервер не найден', show_alert=True)
        return
    user = get_user_by_telegram_id(user_telegram_id)
    email = generate_unique_email(user)
    try:
        client = get_client_from_server_data(server)
        flow = await client.get_inbound_flow(inbound_id)
        result = await client.add_client(inbound_id=inbound_id, email=email, total_gb=traffic_gb, expire_days=days, limit_ip=1, tg_id=str(user_telegram_id), flow=flow)
        client_uuid = result['uuid']
        from database.requests import get_admin_tariff
        admin_tariff = get_admin_tariff()
        tariff_id = admin_tariff['id']
        key_id = create_vpn_key_admin(user_id=user_id, server_id=server_id, tariff_id=tariff_id, panel_inbound_id=inbound_id, panel_email=email, client_uuid=client_uuid, days=days)
        await callback.answer('✅ Ключ успешно создан!', show_alert=True)
        await _show_user_view_edit(callback, state, user_telegram_id)
    except VPNAPIError as e:
        logger.error(f'Ошибка создания ключа: {e}')
        await callback.answer(f'❌ Ошибка: {e}', show_alert=True)
    except Exception as e:
        logger.error(f'Неожиданная ошибка: {e}')
        await callback.answer('❌ Ошибка при создании ключа', show_alert=True)

@router.callback_query(F.data == 'admin_user_add_key_cancel')
async def cancel_add_key(callback: CallbackQuery, state: FSMContext):
    """Отмена добавления ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    data = await state.get_data()
    user_telegram_id = data.get('add_key_user_telegram_id') or data.get('current_user_telegram_id')
    if user_telegram_id:
        await _show_user_view_edit(callback, state, user_telegram_id)
    else:
        await show_users_menu(callback, state)

@router.callback_query(F.data == 'admin_add_key_back')
async def add_key_back(callback: CallbackQuery, state: FSMContext):
    """Шаг назад при добавлении ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer('⛔ Доступ запрещён', show_alert=True)
        return
    current_state = await state.get_state()
    data = await state.get_data()
    if current_state == AdminStates.add_key_inbound.state:
        servers = get_active_servers()
        await state.set_state(AdminStates.add_key_server)
        user = get_user_by_telegram_id(data.get('add_key_user_telegram_id'))
        await callback.message.edit_text(f"➕ *Добавление ключа для {(format_user_display(user) if user else '?')}*\n\nВыберите сервер:", reply_markup=add_key_server_kb(servers), parse_mode='Markdown')
    else:
        await cancel_add_key(callback, state)
    await callback.answer()