"""
Роутер раздела «Реферальная система».

Настройка реферальной программы:
- Включение/выключение
- Режим начисления (дни/баланс)
- Настройка уровней (1-3)
- Текст условий
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import (
    is_referral_enabled,
    get_referral_reward_type,
    get_referral_conditions_text,
    get_referral_levels,
    update_referral_level,
    update_referral_setting,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.keyboards.admin import (
    referral_main_kb,
    referral_level_kb,
    referral_back_kb,
    back_and_home_kb
)

logger = logging.getLogger(__name__)

router = Router()


async def show_referral_menu(callback: CallbackQuery, state: FSMContext):
    """Показывает главное меню реферальной системы."""
    await state.set_state(AdminStates.referral_menu)
    
    enabled = is_referral_enabled()
    reward_type = get_referral_reward_type()
    levels = get_referral_levels()
    conditions_text = get_referral_conditions_text()
    
    status_emoji = "🟢" if enabled else "⚪"
    status_text = "включена" if enabled else "выключена"
    
    if reward_type == 'days':
        type_text = "📅 Дни к ключу"
    else:
        type_text = "💰 На баланс"
    
    text = (
        f"🔗 *Реферальная система*\n\n"
        f"{status_emoji} Статус: *{status_text}*\n"
        f"📊 Режим начисления: *{type_text}*\n\n"
        f"*Уровни:*\n"
    )
    
    for level in levels:
        level_num = level['level_number']
        percent = level['percent']
        is_enabled = level['enabled']
        status = "✅" if is_enabled else "⚪"
        text += f"{status} Уровень {level_num}: {percent}%\n"
    
    if conditions_text:
        text += f"\n📝 Текст условий задан\n"
    
    text += "\nВыберите действие:"
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=referral_main_kb(enabled, reward_type, levels),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_referral")
async def admin_referral(callback: CallbackQuery, state: FSMContext):
    """Вход в раздел реферальной системы."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await show_referral_menu(callback, state)


@router.callback_query(F.data == "admin_referral_toggle")
async def referral_toggle(callback: CallbackQuery, state: FSMContext):
    """Переключение реферальной системы."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = is_referral_enabled()
    new_value = '0' if current else '1'
    update_referral_setting('referral_enabled', new_value)
    
    status = "включена ✅" if new_value == '1' else "выключена"
    await callback.answer(f"Реферальная система {status}")
    
    await show_referral_menu(callback, state)


@router.callback_query(F.data == "admin_referral_toggle_type")
async def referral_toggle_type(callback: CallbackQuery, state: FSMContext):
    """Переключение режима начисления."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = get_referral_reward_type()
    new_value = 'balance' if current == 'days' else 'days'
    update_referral_setting('referral_reward_type', new_value)
    
    if new_value == 'days':
        await callback.answer("Режим: Дни к ключу")
    else:
        await callback.answer("Режим: На баланс")
    
    await show_referral_menu(callback, state)


@router.callback_query(F.data.regexp(r"^admin_referral_level:(\d+)$"))
async def referral_level_view(callback: CallbackQuery, state: FSMContext):
    """Просмотр уровня."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    level_num = int(callback.data.split(':')[1])
    levels = get_referral_levels()
    
    level = None
    for l in levels:
        if l['level_number'] == level_num:
            level = l
            break
    
    if not level:
        await callback.answer("Уровень не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.referral_level_edit)
    await state.update_data(current_level=level_num)
    
    status = "включён" if level['enabled'] else "выключен"
    
    text = (
        f"📊 *Уровень {level_num}*\n\n"
        f"Процент: *{level['percent']}%*\n"
        f"Статус: *{status}*\n\n"
        "Выберите действие:"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=referral_level_kb(level_num, level['percent'], level['enabled']),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin_referral_level_toggle:(\d+)$"))
async def referral_level_toggle(callback: CallbackQuery, state: FSMContext):
    """Переключение уровня."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    level_num = int(callback.data.split(':')[1])
    levels = get_referral_levels()
    
    level = None
    for l in levels:
        if l['level_number'] == level_num:
            level = l
            break
    
    if not level:
        await callback.answer("Уровень не найден", show_alert=True)
        return
    
    new_enabled = not level['enabled']
    update_referral_level(level_num, level['percent'], new_enabled)
    
    status = "включён ✅" if new_enabled else "выключен"
    await callback.answer(f"Уровень {level_num} {status}")
    
    await referral_level_view(callback, state)


@router.callback_query(F.data.regexp(r"^admin_referral_level_percent:(\d+)$"))
async def referral_level_percent_start(callback: CallbackQuery, state: FSMContext):
    """Запрос нового процента для уровня."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    level_num = int(callback.data.split(':')[1])
    levels = get_referral_levels()
    
    level = None
    for l in levels:
        if l['level_number'] == level_num:
            level = l
            break
    
    if not level:
        await callback.answer("Уровень не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.referral_level_edit)
    await state.update_data(
        editing_level_percent=level_num,
        editing_level_message=callback.message
    )
    
    text = (
        f"📊 *Уровень {level_num}*\n\n"
        f"Текущий процент: *{level['percent']}%*\n\n"
        "Введите новый процент (1-100):"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=referral_back_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.referral_level_edit)
async def referral_level_percent_input(message: Message, state: FSMContext):
    """Обработка ввода нового процента."""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    level_num = data.get('editing_level_percent')
    editing_message = data.get('editing_level_message')
    
    if not level_num:
        return
    
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
    
    text = get_message_text_for_storage(message, 'plain')
    
    if not text.isdigit() or not (1 <= int(text) <= 100):
        await message.answer("❌ Введите число от 1 до 100:")
        return
    
    new_percent = int(text)
    levels = get_referral_levels()
    
    level = None
    for l in levels:
        if l['level_number'] == level_num:
            level = l
            break
    
    if level:
        update_referral_level(level_num, new_percent, level['enabled'])
    
    try:
        await message.delete()
    except:
        pass
    
    await state.update_data(editing_level_percent=None, editing_level_message=None)
    
    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
            self.data = f"admin_referral_level:{level_num}"
        async def answer(self, *args, **kwargs):
            pass
    
    fake = FakeCallback(editing_message, message.from_user)
    await referral_level_view(fake, state)


@router.callback_query(F.data == "admin_referral_conditions")
async def referral_conditions_start(callback: CallbackQuery, state: FSMContext):
    """Редактирование текста условий."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from bot.utils.text import format_text_for_edit
    
    await state.set_state(AdminStates.referral_conditions_text)
    
    current_text = get_referral_conditions_text()
    
    await state.update_data(editing_message=callback.message)
    
    await safe_edit_or_send(callback.message, 
        format_text_for_edit("Текст условий реферальной программы", current_text or "Не задано"),
        reply_markup=referral_back_kb(),
        parse_mode="MarkdownV2"
    )
    await callback.answer()


@router.message(AdminStates.referral_conditions_text, ~F.text.startswith('/'))
async def referral_conditions_input(message: Message, state: FSMContext):
    """Обработка ввода текста условий."""
    if not is_admin(message.from_user.id):
        return
    
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send, format_text_after_save
    from bot.keyboards.admin import back_and_home_kb
    
    data = await state.get_data()
    editing_message = data.get('editing_message')
    
    new_text = get_message_text_for_storage(message, 'markdown')
    
    if new_text.lower() in ['пусто', 'empty', '-', '']:
        update_referral_setting('referral_conditions_text', '')
        saved_text = "Не задано"
    else:
        update_referral_setting('referral_conditions_text', new_text)
        saved_text = new_text
    
    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except:
        pass
    
    await state.clear()
    
    # Редактируем сообщение с новым текстом
    if editing_message:
        try:
            await safe_edit_or_send(editing_message, 
                format_text_after_save("Текст условий реферальной программы", saved_text),
                reply_markup=back_and_home_kb("admin_referral"),
                parse_mode="MarkdownV2"
            )
        except:
            await message.answer(
                format_text_after_save("Текст условий реферальной программы", saved_text),
                reply_markup=back_and_home_kb("admin_referral"),
                parse_mode="MarkdownV2"
            )
    else:
        await message.answer(
            format_text_after_save("Текст условий реферальной программы", saved_text),
            reply_markup=back_and_home_kb("admin_referral"),
            parse_mode="MarkdownV2"
        )
