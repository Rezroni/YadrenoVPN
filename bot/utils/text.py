from aiogram.types import Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAnimation
from aiogram.exceptions import TelegramBadRequest
from typing import Literal, Optional, Union
import logging

logger = logging.getLogger(__name__)


TEXT_SEPARATOR_RAW = "=" * 30
TEXT_SEPARATOR = TEXT_SEPARATOR_RAW.replace("=", "\\=")


def format_text_for_edit(title: str, current_text: str) -> str:
    title_escaped = escape_md2(title)
    return (
        f"✏️ *Редактирование: {title_escaped}*\n\n"
        f"📜 *Текущее значение:*\n"
        f"{TEXT_SEPARATOR}\n"
        f"{current_text}\n"
        f"{TEXT_SEPARATOR}\n\n"
        f"👇 Отправьте новое значение \\(или нажмите Отмена\\)\\."
    )


def format_text_after_save(title: str, new_text: str) -> str:
    title_escaped = escape_md2(title)
    return (
        f"✅ *Сохранено: {title_escaped}*\n\n"
        f"📜 *Новое значение:*\n"
        f"{TEXT_SEPARATOR}\n"
        f"{new_text}\n"
        f"{TEXT_SEPARATOR}"
    )


def get_message_text_for_storage(
    message: Message,
    text_type: Literal['markdown', 'plain'] = 'markdown'
) -> str:
    if text_type == 'markdown':
        return message.md_text.strip() if message.md_text else (message.text.strip() if message.text else "")
    else:
        return message.text.strip() if message.text else ""


def escape_md(text: str) -> str:
    if not text:
        return ""
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[").replace("]", "\\]")


def escape_md2(text: str) -> str:
    if not text:
        return ""
    chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in chars:
        text = text.replace(char, '\\' + char)
    return text


def escape_markdown_url(url: str) -> str:
    if not url:
        return url
    url = url.replace('\\', '\\\\')
    url = url.replace(')', '\\)')
    return url


async def safe_edit_or_send(
    message: Message,
    text: str = None,
    reply_markup=None,
    parse_mode: Optional[str] = None,
    photo: Optional[Union[str, object]] = None,
) -> Message:
    """Универсальная функция редактирования/отправки сообщения.
    
    Автоматически определяет тип текущего сообщения и целевой формат,
    выбирая оптимальную стратегию:
    
    - текст → текст: edit_text
    - медиа → текст: удалить + answer (текст)
    - текст → медиа: удалить + answer_photo
    - медиа → медиа: edit_media + edit_caption
    
    Обрабатывает ошибки Telegram API:
    - 'there is no text in the message to edit'
    - 'message is not modified'
    
    Args:
        message: Сообщение для редактирования
        text: Текст сообщения (или caption для медиа)
        reply_markup: Клавиатура
        parse_mode: Режим парсинга (Markdown, HTML, MarkdownV2)
        photo: Фото (file_id, URL или InputFile). Если передано — отправляем медиа-сообщение
    """
    is_current_media = bool(message.photo or message.video or message.document or message.animation)
    want_media = photo is not None
    
    try:
        if want_media and is_current_media:
            # Медиа → Медиа: редактируем media + caption
            input_media = InputMediaPhoto(media=photo, caption=text, parse_mode=parse_mode)
            result = await message.edit_media(media=input_media, reply_markup=reply_markup)
            return result
            
        elif want_media and not is_current_media:
            # Текст → Медиа: удаляем текст, отправляем фото
            try:
                await message.delete()
            except Exception:
                pass
            return await message.answer_photo(
                photo=photo, caption=text,
                reply_markup=reply_markup, parse_mode=parse_mode
            )
            
        elif not want_media and not is_current_media:
            # Текст → Текст: обычное редактирование
            return await message.edit_text(
                text=text, reply_markup=reply_markup, parse_mode=parse_mode
            )
            
        else:
            # Медиа → Текст: удаляем медиа, отправляем текст
            try:
                await message.delete()
            except Exception:
                pass
            return await message.answer(
                text=text, reply_markup=reply_markup, parse_mode=parse_mode
            )
            
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        
        if 'message is not modified' in error_msg:
            # Содержимое не изменилось — игнорируем
            logger.debug('Сообщение не изменено, пропускаем')
            return message
            
        if 'there is no text in the message' in error_msg or \
           'message can\'t be edited' in error_msg or \
           'there is no media in the message' in error_msg:
            # Фоллбэк: удаляем и отправляем заново
            try:
                await message.delete()
            except Exception:
                pass
            if want_media:
                return await message.answer_photo(
                    photo=photo, caption=text,
                    reply_markup=reply_markup, parse_mode=parse_mode
                )
            else:
                return await message.answer(
                    text=text, reply_markup=reply_markup, parse_mode=parse_mode
                )
        raise

