"""
Универсальный редактор сообщений для админ-панели.

Утилиты для хранения, отображения и редактирования сообщений.
Сообщение = текст + опциональное медиа (фото, видео, гифка).
В БД хранится JSON: {text, photo_file_id, video_file_id, animation_file_id}.
Обратная совместимость: если в БД старая строка (не JSON) — возвращаем {'text': строка}.
"""
import json
import logging
from typing import Optional, List

from aiogram.types import (
    Message, InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext

from database.requests import get_setting, set_setting

logger = logging.getLogger(__name__)

# Допустимые типы медиа
ALL_MEDIA_TYPES = ['text', 'photo', 'video', 'animation']


def get_message_data(key: str, default_text: str = '') -> dict:
    """
    Загружает данные сообщения из settings.
    
    Обратная совместимость: если в БД старая строка (не JSON),
    возвращаем {'text': строка}.
    
    Args:
        key: Ключ настройки в таблице settings
        default_text: Текст по умолчанию если ключ не найден
        
    Returns:
        Словарь с ключами: text, photo_file_id, video_file_id, animation_file_id
    """
    raw = get_setting(key)
    
    if raw is None:
        return {'text': default_text}
    
    # Пробуем распарсить как JSON
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and 'text' in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Старый формат — просто строка
    return {'text': raw}


def save_message_data(key: str, message: Message, allowed_types: Optional[List[str]] = None) -> dict:
    """
    Извлекает данные из входящего Telegram-сообщения и сохраняет в settings как JSON.
    
    Использует get_message_text_for_storage() для текста (правило ТЗ).
    Медиа не скачиваем — сохраняем только file_id.
    
    Args:
        key: Ключ настройки
        message: Входящее сообщение от Telegram
        allowed_types: Допустимые типы ['text', 'photo', 'video', 'animation']
        
    Returns:
        Сохранённый словарь данных
    """
    from bot.utils.text import get_message_text_for_storage
    
    data = {
        'text': '',
        'photo_file_id': None,
        'video_file_id': None,
        'animation_file_id': None,
    }
    
    # Определяем тип сообщения и извлекаем медиа
    if message.animation:
        data['animation_file_id'] = message.animation.file_id
        # Для медиа используем caption
        data['text'] = get_message_text_for_storage(message, 'markdown') if message.caption else ''
    elif message.video:
        data['video_file_id'] = message.video.file_id
        data['text'] = get_message_text_for_storage(message, 'markdown') if message.caption else ''
    elif message.photo:
        data['photo_file_id'] = message.photo[-1].file_id
        data['text'] = get_message_text_for_storage(message, 'markdown') if message.caption else ''
    elif message.text:
        data['text'] = get_message_text_for_storage(message, 'markdown')
    
    # Сохраняем как JSON
    set_setting(key, json.dumps(data, ensure_ascii=False))
    logger.info(f"Сообщение сохранено: {key}")
    
    return data


def detect_message_type(message: Message) -> str:
    """
    Определяет тип входящего сообщения.
    
    Returns:
        'animation', 'video', 'photo' или 'text'
    """
    if message.animation:
        return 'animation'
    if message.video:
        return 'video'
    if message.photo:
        return 'photo'
    return 'text'


def editor_kb(back_callback: str, has_help: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура редактора сообщений.
    
    Раскладка:
    [⬅️ Назад]  [🈴 На главную]
    [📝 Отправьте новое сообщение ⬇️]
    
    Args:
        back_callback: callback_data для кнопки «Назад»
        has_help: Есть ли текст справки (меняет поведение кнопки)
    """
    builder = InlineKeyboardBuilder()
    
    # Верхний ряд: Назад + На главную
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton(text="🈴 На главную", callback_data="start"),
    )
    
    # Нижний ряд: кнопка ввода
    if has_help:
        # Кнопка показывает справку перед вводом
        builder.row(
            InlineKeyboardButton(
                text="📝 Отправьте новое сообщение ⬇️",
                callback_data="msg_editor_show_help"
            )
        )
    else:
        # Кнопка-заглушка (просто визуальный индикатор, редактор уже ждёт ввод)
        builder.row(
            InlineKeyboardButton(
                text="📝 Отправьте новое сообщение ⬇️",
                callback_data="noop"
            )
        )
    
    return builder.as_markup()


def editor_help_kb() -> InlineKeyboardMarkup:
    """Клавиатура для экрана справки редактора."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="msg_editor_back_to_preview")
    )
    return builder.as_markup()
