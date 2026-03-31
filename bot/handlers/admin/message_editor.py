"""
Роутер универсального редактора сообщений.

Обрабатывает:
- Входящие сообщения в состоянии waiting_for_message
- Callback кнопки справки (msg_editor_show_help)
- Callback кнопки возврата к превью (msg_editor_back_to_preview)
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.text import safe_edit_or_send
from bot.utils.message_editor import (
    get_message_data, save_message_data, detect_message_type,
    editor_kb, editor_help_kb,
)

logger = logging.getLogger(__name__)

from bot.utils.message_editor import get_message_data

router = Router()


async def show_message_editor(
    message: Message,
    state: FSMContext,
    key: str,
    back_callback: str,
    help_text: str = None,
    allowed_types: list = None,
    parse_mode: str = None,
) -> Message:
    """
    Показывает превью сообщения с кнопками редактора.
    
    Превью = сообщение ровно так, как оно будет выглядеть для пользователя.
    Без заголовков, рамок и инструкций.
    
    Использует safe_edit_or_send() для рендера (правило ТЗ).
    Сохраняет контекст в FSM data.
    
    Args:
        message: Сообщение для редактирования (callback.message или результат answer)
        state: FSM контекст
        key: Ключ настройки в settings
        back_callback: callback_data для кнопки «Назад»
        help_text: Текст справки (опционально)
        allowed_types: Допустимые типы медиа (по умолчанию все)
    
    Returns:
        Объект Message после рендера (для сохранения в FSM)
    """
    if allowed_types is None:
        allowed_types = ['text', 'photo', 'video', 'animation']
    
    # Загружаем данные из БД
    data = get_message_data(key)
    text = data.get('text', '') or '_(пусто)_'
    photo = data.get('photo_file_id')
    video = data.get('video_file_id')
    animation = data.get('animation_file_id')
    
    # Формируем клавиатуру редактора
    kb = editor_kb(back_callback, has_help=bool(help_text))
    
    # Определяем медиа для показа (приоритет: animation > video > photo)
    # safe_edit_or_send пока поддерживает только photo, поэтому для video/animation
    # используем фоллбэк на текст с пометкой
    media_file_id = None
    if animation:
        # GIF — отправляем как текст (safe_edit_or_send не поддерживает animation)
        # TODO: расширить safe_edit_or_send для animation/video
        text = f"{text}\n\n🎞 _(к сообщению прикреплена GIF)_"
    elif video:
        text = f"{text}\n\n🎬 _(к сообщению прикреплено видео)_"
    elif photo:
        media_file_id = photo
    
    # Определяем режим парсинга
    used_parse_mode = parse_mode
    if used_parse_mode is None:
        used_parse_mode = 'MarkdownV2' if not media_file_id else 'Markdown'
        
    # Показываем превью через safe_edit_or_send
    result = await safe_edit_or_send(
        message, text,
        reply_markup=kb,
        parse_mode=used_parse_mode,
        photo=media_file_id,
    )
    
    # Сохраняем контекст в FSM
    await state.set_state(AdminStates.waiting_for_message)
    await state.update_data(
        editing_key=key,
        editor_message=result,  # Message объект для перерисовки
        back_callback=back_callback,
        allowed_types=allowed_types,
        help_text=help_text,
        editor_parse_mode=parse_mode,
    )
    
    return result


# ============================================================================
# CALLBACK: СПРАВКА РЕДАКТОРА
# ============================================================================

@router.callback_query(F.data == "msg_editor_show_help")
async def show_editor_help(callback: CallbackQuery, state: FSMContext):
    """Показывает справку редактора (если help_text передан)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    help_text = data.get('help_text', '')
    
    if not help_text:
        await callback.answer()
        return
    
    # Показываем справку (остаёмся в waiting_for_message — ввод работает)
    result = await safe_edit_or_send(
        callback.message,
        help_text,
        reply_markup=editor_help_kb(),
        parse_mode='Markdown',
    )
    
    # Обновляем сохранённое сообщение
    await state.update_data(editor_message=result)
    await callback.answer()


@router.callback_query(F.data == "msg_editor_back_to_preview")
async def back_to_preview(callback: CallbackQuery, state: FSMContext):
    """Возврат к превью из справки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    key = data.get('editing_key')
    back_callback = data.get('back_callback')
    help_text = data.get('help_text')
    allowed_types = data.get('allowed_types')
    parse_mode = data.get('editor_parse_mode')
    
    if not key:
        await callback.answer("❌ Ошибка состояния", show_alert=True)
        return
    
    # Перерисовываем превью
    await show_message_editor(
        callback.message, state,
        key=key,
        back_callback=back_callback,
        help_text=help_text,
        allowed_types=allowed_types,
        parse_mode=parse_mode,
    )
    await callback.answer()


# ============================================================================
# MESSAGE HANDLER: ПРИЁМ НОВОГО СООБЩЕНИЯ
# ============================================================================

@router.message(AdminStates.waiting_for_message, ~F.text.startswith('/'))
async def handle_editor_input(message: Message, state: FSMContext):
    """
    Обрабатывает входящее сообщение при редактировании.
    
    1. Проверяет тип сообщения vs allowed_types
    2. Сохраняет в БД через save_message_data()
    3. Удаляет сообщение пользователя
    4. Перерисовывает превью (без уведомления «Сохранено»)
    """
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    key = data.get('editing_key')
    back_callback = data.get('back_callback')
    help_text = data.get('help_text')
    allowed_types = data.get('allowed_types', ['text', 'photo', 'video', 'animation'])
    editor_message = data.get('editor_message')
    parse_mode = data.get('editor_parse_mode')
    
    if not key:
        await state.clear()
        await message.answer("❌ Ошибка состояния.")
        return
    
    # Проверяем тип сообщения
    msg_type = detect_message_type(message)
    if msg_type not in allowed_types:
        # Молча удаляем неподходящее сообщение 
        try:
            await message.delete()
        except Exception:
            pass
        return
    
    # Сохраняем в БД
    save_message_data(key, message, allowed_types)
    
    # Удаляем сообщение пользователя (паттерн из AGENTS.md)
    try:
        await message.delete()
    except Exception:
        pass
    
    # Перерисовываем превью на месте старого сообщения
    if editor_message:
        try:
            result = await show_message_editor(
                editor_message, state,
                key=key,
                back_callback=back_callback,
                help_text=help_text,
                allowed_types=allowed_types,
                parse_mode=parse_mode,
            )
            return
        except Exception as e:
            logger.warning(f"Ошибка перерисовки превью: {e}")
    
    # Фоллбэк: отправляем новое сообщение
    result = await show_message_editor(
        message, state,
        key=key,
        back_callback=back_callback,
        help_text=help_text,
        allowed_types=allowed_types,
        parse_mode=parse_mode,
    )
