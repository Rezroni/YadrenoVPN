from aiogram.types import Message
from typing import Literal


TEXT_SEPARATOR_RAW = "=" * 30
TEXT_SEPARATOR = TEXT_SEPARATOR_RAW.replace("=", "\\=")


def format_text_for_edit(title: str, current_text: str) -> str:
    title_escaped = escape_md2(title)
    return (
        f"\u270f\ufe0f *\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435: {title_escaped}*\n\n"
        f"\U0001F4DC *\u0422\u0435\u043a\u0443\u0449\u0435\u0435 \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435:*\n"
        f"{TEXT_SEPARATOR}\n"
        f"{current_text}\n"
        f"{TEXT_SEPARATOR}\n\n"
        f"\U0001F447 \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u043d\u043e\u0432\u043e\u0435 \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \\(\u0438\u043b\u0438 \u043d\u0430\u0436\u043c\u0438\u0442\u0435 \u041e\u0442\u043c\u0435\u043d\u0430\\)\\."
    )


def format_text_after_save(title: str, new_text: str) -> str:
    title_escaped = escape_md2(title)
    return (
        f"\u2705 *\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e: {title_escaped}*\n\n"
        f"\U0001F4DC *\u041d\u043e\u0432\u043e\u0435 \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435:*\n"
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
