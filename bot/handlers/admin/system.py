"""
Обработчики раздела «Настройки бота».

Управление обновлением, остановкой бота и редактированием текстов.
"""
import asyncio
import logging
import os
import sys
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

from config import GITHUB_REPO_URL
from bot.utils.admin import is_admin
from bot.utils.git_utils import (
    check_git_available,
    get_current_commit,
    get_current_branch,
    get_remote_url,
    set_remote_url,
    check_for_updates,
    pull_updates,
    pull_to_commit,
    force_pull_updates,
    get_last_commit_info,
    get_previous_commits_info,
    restart_bot,
)
from bot.keyboards.admin import (
    bot_settings_kb,
    update_confirm_kb,
    force_overwrite_confirm_kb,
    stop_bot_confirm_kb,
    back_and_home_kb,
    admin_logs_menu_kb,
)

logger = logging.getLogger(__name__)

from bot.utils.text import safe_edit_or_send

router = Router()


# ============================================================================
# ГЛАВНОЕ МЕНЮ НАСТРОЕК
# ============================================================================

@router.callback_query(F.data == "admin_bot_settings")
async def show_bot_settings(callback: CallbackQuery, state: FSMContext):
    """Показывает меню настроек бота."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Информация о текущей версии
    commit = get_current_commit() or "неизвестно"
    branch = get_current_branch() or "неизвестно"
    
    # Проверяем настроен ли GitHub
    github_status = "✅ Настроен" if GITHUB_REPO_URL else "❌ Не настроен"
    
    text = (
        "⚙️ *Настройки бота*\n\n"
        f"📌 Версия: `{commit}`\n"
        f"🌿 Ветка: `{branch}`\n"
        f"🔗 GitHub: {github_status}\n\n"
        "Выберите действие:"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=bot_settings_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()






# ============================================================================
# РУЧНОЕ ОБНОВЛЕНИЕ БОТА (КОМАНДОЙ /UPDATE)
# ============================================================================

@router.message(Command("update"))
async def admin_update_cmd(message: Message, state: FSMContext):
    """Скрытая команда экстренного обновления для администраторов."""
    if not is_admin(message.from_user.id):
        return
        
    # Проверяем и обновляем remote URL если нужно
    current_remote = get_remote_url()
    if current_remote != GITHUB_REPO_URL and GITHUB_REPO_URL:
        set_remote_url(GITHUB_REPO_URL)
        
    await message.answer(
        "🔄 *Экстренное обновление...*\n\n"
        "Загружаю изменения с GitHub...",
        parse_mode="Markdown"
    )
    
    success, log_message = pull_updates()
    
    if not success:
        await message.answer(
            f"❌ *Ошибка обновления*\n\n{log_message}",
            parse_mode="Markdown"
        )
        return
        
    logger.info(f"🔄 Бот экстренно обновлён администратором {message.from_user.id} через команду /update")
    
    await message.answer(
        f"✅ *Обновление завершено!*\n\n{log_message}\n\n"
        "🔄 Перезапуск бота через 2 секунды...",
        parse_mode="Markdown"
    )
    
    await state.clear()
    await asyncio.sleep(2)
    restart_bot()


# ============================================================================
# ОБНОВЛЕНИЕ БОТА (ИНТЕРФЕЙС)
# ============================================================================

@router.callback_query(F.data == "admin_update_bot")
async def show_update_confirm(callback: CallbackQuery, state: FSMContext):
    """Показывает подтверждение обновления."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем настроен ли GitHub
    if not GITHUB_REPO_URL:
        await safe_edit_or_send(callback.message, 
            "❌ *GitHub не настроен*\n\n"
            "Укажите URL репозитория в файле `config.py`:\n"
            "`GITHUB_REPO_URL = \"https://github.com/user/repo.git\"`",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    # Проверяем и обновляем remote URL если нужно
    current_remote = get_remote_url()
    if current_remote != GITHUB_REPO_URL:
        set_remote_url(GITHUB_REPO_URL)
    
    # Показываем сообщение о проверке
    await safe_edit_or_send(callback.message, 
        "🔍 *Проверка обновлений...*\n\n"
        "Подключаюсь к GitHub...",
        parse_mode="Markdown"
    )
    
    # Проверяем наличие обновлений
    success, commits_behind, log_text, has_blocking, blocking_commit, is_beta_only = check_for_updates()
    
    if not success:
        await safe_edit_or_send(callback.message, 
            f"❌ *Ошибка проверки*\n\n{log_text}",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    commit_hash = get_current_commit() or "неизвестно"
    
    if commits_behind > 0:
        branch = get_current_branch() or "main"
        target_rev = f"origin/{branch}"
    else:
        target_rev = "HEAD"
        
    last_commit = get_last_commit_info(target_rev)
    previous_commits = get_previous_commits_info(5, target_rev)
    
    # Формируем текст с коммитами
    commits_text = f"🔹 *Последний коммит:*\n```\n{last_commit}\n```\n"
    if previous_commits != "Нет предыдущих коммитов":
         commits_text += f"\n🔸 *Предыдущие 5 коммитов:*\n```\n{previous_commits}\n```"
    
    # Сохраняем данные о блокирующем коммите в FSM state
    await state.update_data(
        has_blocking=has_blocking,
        blocking_commit=blocking_commit
    )
    
    # Если обновлений нет
    if commits_behind == 0:
        await safe_edit_or_send(callback.message, 
            "✅ *Обновление не требуется, у вас последняя версия*\n\n"
            f"Текущая версия: `{commit_hash}`\n\n"
            f"{commits_text}",
            reply_markup=update_confirm_kb(has_updates=False),
            parse_mode="Markdown"
        )
    elif has_blocking and blocking_commit:
        # Есть блокирующее обновление — показываем предупреждение
        # Убираем маркер ! из сообщения при отображении
        blocking_msg = blocking_commit['message'].lstrip('!')
        blocking_hash = blocking_commit['hash'][:8]
        
        await safe_edit_or_send(callback.message, 
            f"⚠️ *Блокирующее обновление!*\n\n"
            f"📦 *Доступно обновлений:* {commits_behind}\n"
            f"Текущая версия: `{commit_hash}`\n\n"
            f"🚫 Среди обновлений найден *блокирующий коммит* `{blocking_hash}`:\n"
            f"```\n{blocking_msg}\n```\n\n"
            f"Будет установлен *только этот коммит*. "
            f"После перезапуска вам потребуется выполнить требуемые действия, "
            f"прежде чем обновляться дальше.\n\n"
            f"{commits_text}",
            reply_markup=update_confirm_kb(has_updates=True, has_blocking=True),
            parse_mode="Markdown"
        )
    elif is_beta_only:
        # Только бета-обновления
        await safe_edit_or_send(callback.message, 
            f"🧪 *Доступна бета-версия!*\n\n"
            f"📦 *Доступно бета-коммитов:* {commits_behind}\n"
            f"Текущая версия: `{commit_hash}`\n\n"
            f"{commits_text}\n\n"
            "⚠️ Это тестовая версия. Устанавливайте на свой страх и риск.",
            reply_markup=update_confirm_kb(has_updates=True, has_blocking=False, is_beta_only=True),
            parse_mode="Markdown"
        )
    else:
        # Есть обычные обновления
        await safe_edit_or_send(callback.message, 
            f"📦 *Доступно обновлений:* {commits_behind}\n\n"
            f"Текущая версия: `{commit_hash}`\n\n"
            f"{commits_text}\n\n"
            "⚠️ После обновления бот автоматически перезапустится.\n"
            "Это займёт несколько секунд.",
            reply_markup=update_confirm_kb(has_updates=True, has_blocking=False, is_beta_only=False),
            parse_mode="Markdown"
        )
    
    await callback.answer()


@router.callback_query(F.data == "admin_update_bot_confirm")
async def update_bot_confirmed(callback: CallbackQuery, state: FSMContext):
    """Выполняет обновление и перезапуск бота."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем и обновляем remote URL если нужно
    current_remote = get_remote_url()
    if current_remote != GITHUB_REPO_URL:
        set_remote_url(GITHUB_REPO_URL)
    
    # Получаем данные о блокирующем коммите из FSM state
    data = await state.get_data()
    has_blocking = data.get('has_blocking', False)
    blocking_commit = data.get('blocking_commit')
    
    if has_blocking and blocking_commit:
        # Блокирующее обновление — обновляем до конкретного коммита
        await safe_edit_or_send(callback.message, 
            "🔄 *Блокирующее обновление...*\n\n"
            f"Обновляю до коммита `{blocking_commit['hash'][:8]}`...",
            parse_mode="Markdown"
        )
        
        success, message = pull_to_commit(blocking_commit['hash'])
    else:
        # Обычное обновление — git pull
        await safe_edit_or_send(callback.message, 
            "🔄 *Обновление...*\n\n"
            "Загружаю изменения с GitHub...",
            parse_mode="Markdown"
        )
        
        success, message = pull_updates()
    
    if not success:
        await safe_edit_or_send(callback.message, 
            f"❌ *Ошибка обновления*\n\n{message}",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    # Успешное обновление — показываем лог и перезапускаем
    logger.info(f"🔄 Бот обновлён администратором {callback.from_user.id}")
    
    if has_blocking:
        await safe_edit_or_send(callback.message, 
            f"✅ *Блокирующее обновление завершено!*\n\n{message}\n\n"
            "⚠️ После перезапуска выполните требуемые действия перед следующим обновлением.\n\n"
            "🔄 Перезапуск бота через 2 секунды...",
            parse_mode="Markdown"
        )
    else:
        await safe_edit_or_send(callback.message, 
            f"✅ *Обновление завершено!*\n\n{message}\n\n"
            "🔄 Перезапуск бота через 2 секунды...",
            parse_mode="Markdown"
        )
    
    await callback.answer("Бот перезапускается...", show_alert=True)
    
    # Очищаем FSM state
    await state.clear()
    
    # Даём время на отправку сообщения
    await asyncio.sleep(2)
    
    # Перезапускаем бота
    restart_bot()



@router.callback_query(F.data == "admin_force_overwrite")
async def show_force_overwrite(callback: CallbackQuery, state: FSMContext):
    """Показывает предупреждение перед принудительной перезаписью."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем настроен ли GitHub
    if not GITHUB_REPO_URL:
        await safe_edit_or_send(callback.message, 
            "❌ *GitHub не настроен*\n\n"
            "Укажите URL репозитория в файле `config.py`:\n"
            "`GITHUB_REPO_URL = \"https://github.com/user/repo.git\"`",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
        
    await safe_edit_or_send(callback.message, 
        "⚠️ *ПРИНУДИТЕЛЬНАЯ ПЕРЕЗАПИСЬ*\n\n"
        f"Все файлы бота (кроме конфигурации и баз данных) будут перезаписаны оригинальными файлами из репозитория:\n`{GITHUB_REPO_URL}`\n\n"
        "🛑 *Внимание: Все ваши локальные изменения в коде будут безвозвратно потеряны!*\n\n"
        "Вы действительно хотите продолжить?",
        reply_markup=force_overwrite_confirm_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_force_overwrite_confirm")
async def force_overwrite_confirmed(callback: CallbackQuery, state: FSMContext):
    """Выполняет принудительную перезапись и перезапуск бота."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем и обновляем remote URL если нужно
    current_remote = get_remote_url()
    if current_remote != GITHUB_REPO_URL and GITHUB_REPO_URL:
        set_remote_url(GITHUB_REPO_URL)
    
    await safe_edit_or_send(callback.message, 
        "🔄 *Принудительная перезапись...*\n\n"
        "Связываюсь с репозиторием и перезаписываю файлы. Пожалуйста, подождите...",
        parse_mode="Markdown"
    )
    
    # Выполняем принудительный git fetch и reset
    success, message = force_pull_updates()
    
    if not success:
        await safe_edit_or_send(callback.message, 
            f"❌ *Ошибка перезаписи*\n\n{message}",
            reply_markup=back_and_home_kb("admin_bot_settings"),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    logger.info(f"🔄 Бот принудительно перезаписан администратором {callback.from_user.id}")
    
    await safe_edit_or_send(callback.message, 
        f"✅ *Успешно!*\n\n{message}\n\n"
        "🔄 Перезапуск бота через 2 секунды...",
        parse_mode="Markdown"
    )
    await callback.answer("Бот перезапускается...", show_alert=True)
    
    # Даём время на отправку сообщения
    await asyncio.sleep(2)
    
    # Перезапускаем бота
    restart_bot()


# ============================================================================
# ИЗМЕНЕНИЕ ТЕКСТОВ (ЗАГЛУШКА)
# ============================================================================

# ============================================================================
# ИЗМЕНЕНИЕ ТЕКСТОВ
# ============================================================================

from bot.states.admin_states import AdminStates

@router.callback_query(F.data == "admin_edit_texts")
async def edit_texts_menu(callback: CallbackQuery, state: FSMContext):
    """Меню выбора текста для редактирования."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from bot.keyboards.admin import back_and_home_kb
    
    builder = InlineKeyboardBuilder()
    
    builder.row(InlineKeyboardButton(text="📝 Главная страница", callback_data="edit_text:main_page_text"))
    builder.row(InlineKeyboardButton(text="📝 Справка (текст)", callback_data="edit_text:help_page_text"))
    builder.row(InlineKeyboardButton(text="📝 Текст перед оплатой", callback_data="edit_text:prepayment_text"))
    builder.row(InlineKeyboardButton(text="📝 Текст выдачи ключа", callback_data="edit_text:key_delivery_text"))
    builder.row(InlineKeyboardButton(text="📢 Ссылка: Новости", callback_data="edit_link:news"))
    builder.row(InlineKeyboardButton(text="💬 Ссылка: Поддержка", callback_data="edit_link:support"))
    
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_bot_settings"))
    
    await safe_edit_or_send(callback.message, 
        "✏️ *Редактирование текстов*\n\n"
        "Выберите, что хотите изменить:",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_text:"))
async def edit_text_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования конкретного текста через универсальный редактор."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    from bot.handlers.admin.message_editor import show_message_editor
    
    key = callback.data.split(":")[1]
    
    # Белый список допустимых ключей — защита от инъекции произвольного ключа настроек
    ALLOWED_KEYS = {
        'main_page_text',
        'help_page_text',
        'prepayment_text',
        'key_delivery_text',
    }
    
    if key not in ALLOWED_KEYS:
        await callback.answer("⛔ Недопустимый параметр", show_alert=True)
        return
    
    # Тексты справки для каждого ключа
    help_texts = {
        'main_page_text': (
            "📝 *Справка: Текст главной страницы*\n\n"
            "Поддерживается MarkdownV2 форматирование.\n\n"
            "Переменные:\n"
            "• `%тарифы%` — список тарифов с ценами\n"
            "• `%без_тарифов%` — не добавлять тарифы"
        ),
        'key_delivery_text': (
            "📝 *Справка: Текст выдачи ключа*\n\n"
            "Формат: **Только текст** (без фото).\n\n"
            "Переменные:\n"
            "• `%ключ%` — вместо этого тега будет подставлен блок с ссылкой на ключ."
        ),
    }
    
    current_allowed_types = ['text'] if key == 'key_delivery_text' else ['text', 'photo']
    parse_mode = 'Markdown' if key == 'key_delivery_text' else None
    
    await show_message_editor(
        callback.message, state,
        key=key,
        back_callback='admin_edit_texts',
        help_text=help_texts.get(key),
        allowed_types=current_allowed_types,
        parse_mode=parse_mode,
    )
    await callback.answer()


# ============================================================================
# РЕДАКТИРОВАНИЕ КНОПОК-ССЫЛОК
# ============================================================================

@router.callback_query(F.data.startswith("edit_link:"))
async def edit_link_menu(callback: CallbackQuery, state: FSMContext):
    """Меню редактирования кнопки-ссылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from database.requests import get_setting
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    
    link_type = callback.data.split(":")[1]
    
    if link_type not in ('news', 'support'):
        await callback.answer("⛔ Недопустимый параметр", show_alert=True)
        return
    
    # Получаем текущие настройки
    link_key = f"{link_type}_channel_link"
    hidden_key = f"{link_type}_hidden"
    name_key = f"{link_type}_button_name"
    
    current_url = get_setting(link_key, "Не задано")
    is_hidden = get_setting(hidden_key, "0") == "1"
    button_name = get_setting(name_key, "Новости" if link_type == "news" else "Поддержка")
    
    # Названия для заголовка
    titles = {
        'news': 'Новости',
        'support': 'Поддержка'
    }
    
    hidden_status = "👁️ Скрыта" if is_hidden else "👁️‍🗨️ Показывается"
    
    builder = InlineKeyboardBuilder()
    
    builder.row(InlineKeyboardButton(
        text="🔗 Изменить ссылку",
        callback_data=f"edit_link_url:{link_type}"
    ))
    builder.row(InlineKeyboardButton(
        text=f"{'👁️‍🗨️ Показать' if is_hidden else '👁️ Скрыть'} кнопку",
        callback_data=f"toggle_link_hidden:{link_type}"
    ))
    builder.row(InlineKeyboardButton(
        text=f"✏️ Название: {button_name}",
        callback_data=f"edit_link_name:{link_type}"
    ))
    builder.row(InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data="admin_edit_texts"
    ))
    
    await safe_edit_or_send(callback.message, 
        f"🔗 *Редактирование: {titles[link_type]}*\n\n"
        f"📍 *Ссылка:* `{current_url}`\n"
        f"🏷 *Название кнопки:* {button_name}\n"
        f"👀 *Статус:* {hidden_status}",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_link_url:"))
async def edit_link_url_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования URL ссылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from database.requests import get_setting
    from bot.keyboards.admin import cancel_kb
    
    link_type = callback.data.split(":")[1]
    
    if link_type not in ('news', 'support'):
        await callback.answer("⛔ Недопустимый параметр", show_alert=True)
        return
    
    link_key = f"{link_type}_channel_link"
    current_url = get_setting(link_key, "Не задано")
    
    titles = {
        'news': 'Новости',
        'support': 'Поддержка'
    }
    
    await state.set_state(AdminStates.waiting_for_link_url)
    await state.update_data(editing_key=link_key, return_to=f"edit_link:{link_type}", editing_message=callback.message)
    
    await safe_edit_or_send(callback.message, 
        f"🔗 *Изменение ссылки: {titles[link_type]}*\n\n"
        f"📜 *Текущая ссылка:*\n`{current_url}`\n\n"
        f"👇 Отправьте новую ссылку (должна начинаться с http:// или https://):",
        reply_markup=cancel_kb(f"edit_link:{link_type}"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_link_url, ~F.text.startswith('/'))
async def edit_link_url_save(message: Message, state: FSMContext):
    """Сохранение новой ссылки."""
    if not is_admin(message.from_user.id):
        return
    
    from database.requests import set_setting
    from bot.keyboards.admin import back_and_home_kb, cancel_kb
    from bot.utils.text import get_message_text_for_storage
    
    data = await state.get_data()
    key = data.get('editing_key')
    return_to = data.get('return_to', 'admin_edit_texts')
    editing_message = data.get('editing_message')
    
    if not key:
        await state.clear()
        await message.answer("❌ Ошибка состояния.")
        return
    
    new_value = get_message_text_for_storage(message, 'plain')
    
    # Валидация URL
    if not new_value.startswith(('http://', 'https://')):
        await message.answer(
            "❌ *Ошибка:* Ссылка должна начинаться с `http://` или `https://`\n\n"
            f"Вы ввели: `{new_value}`\n\n"
            "Попробуйте ещё раз или нажмите Отмена.",
            reply_markup=cancel_kb(return_to),
            parse_mode="Markdown"
        )
        return
    
    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except Exception:
        pass
    
    set_setting(key, new_value)
    await state.clear()
    
    # Перерисовываем сообщение
    if editing_message:
        try:
            await safe_edit_or_send(editing_message,
                f"✅ *Ссылка сохранена!*\n\n`{new_value}`",
                reply_markup=back_and_home_kb(return_to),
                parse_mode="Markdown"
            )
        except Exception:
            await message.answer(
                f"✅ *Ссылка сохранена!*\n\n`{new_value}`",
                reply_markup=back_and_home_kb(return_to),
                parse_mode="Markdown"
            )
    else:
        await message.answer(
            f"✅ *Ссылка сохранена!*\n\n`{new_value}`",
            reply_markup=back_and_home_kb(return_to),
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("toggle_link_hidden:"))
async def toggle_link_hidden(callback: CallbackQuery, state: FSMContext):
    """Переключение видимости кнопки-ссылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from database.requests import get_setting, set_setting
    
    link_type = callback.data.split(":")[1]
    
    if link_type not in ('news', 'support'):
        await callback.answer("⛔ Недопустимый параметр", show_alert=True)
        return
    
    hidden_key = f"{link_type}_hidden"
    current = get_setting(hidden_key, "0")
    new_value = "1" if current == "0" else "0"
    set_setting(hidden_key, new_value)
    
    # Возвращаемся в меню редактирования ссылки
    await edit_link_menu(callback, state)


@router.callback_query(F.data.startswith("edit_link_name:"))
async def edit_link_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования названия кнопки-ссылки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from database.requests import get_setting
    from bot.keyboards.admin import cancel_kb
    
    link_type = callback.data.split(":")[1]
    
    if link_type not in ('news', 'support'):
        await callback.answer("⛔ Недопустимый параметр", show_alert=True)
        return
    
    name_key = f"{link_type}_button_name"
    current_name = get_setting(name_key, "Новости" if link_type == "news" else "Поддержка")
    
    titles = {
        'news': 'Новости',
        'support': 'Поддержка'
    }
    
    await state.set_state(AdminStates.waiting_for_link_button_name)
    await state.update_data(editing_name_key=name_key, link_type=link_type)
    
    await safe_edit_or_send(callback.message, 
        f"✏️ *Изменение названия кнопки: {titles[link_type]}*\n\n"
        f"🏷 *Текущее название:* {current_name}\n\n"
        f"👇 Отправьте новое название для кнопки (максимум 30 символов):",
        reply_markup=cancel_kb(f"edit_link:{link_type}"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_link_button_name)
async def edit_link_name_save(message: Message, state: FSMContext):
    """Сохранение нового названия кнопки-ссылки."""
    from database.requests import set_setting
    from bot.keyboards.admin import back_and_home_kb
    
    data = await state.get_data()
    name_key = data.get('editing_name_key')
    link_type = data.get('link_type')
    
    if not name_key:
        await state.clear()
        await message.answer("❌ Ошибка состояния.")
        return
    
    from bot.utils.text import get_message_text_for_storage
    
    new_name = get_message_text_for_storage(message, 'plain')[:30]
    
    if len(new_name) < 1:
        await message.answer(
            "❌ *Название не может быть пустым*\n\n"
            "Попробуйте ещё раз или нажмите Отмена.",
            reply_markup=back_and_home_kb(f"edit_link:{link_type}" if link_type else "admin_edit_texts"),
            parse_mode="Markdown"
        )
        return
    
    set_setting(name_key, new_name)
    await state.clear()
    
    await message.answer(
        f"✅ *Название сохранено!*\n\n{new_name}",
        reply_markup=back_and_home_kb(f"edit_link:{link_type}" if link_type else "admin_edit_texts"),
        parse_mode="Markdown"
    )




# ============================================================================
# ОСТАНОВКА БОТА
# ============================================================================

@router.callback_query(F.data == "admin_stop_bot")
async def show_stop_bot_confirm(callback: CallbackQuery, state: FSMContext):
    """Показывает окно подтверждения остановки бота."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await safe_edit_or_send(callback.message, 
        "🛑 *Остановка бота*\n\n"
        "Вы уверены, что хотите остановить бот?\n\n"
        "⚠️ Бот перестанет отвечать на сообщения пользователей "
        "до следующего ручного запуска.",
        reply_markup=stop_bot_confirm_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_stop_bot_confirm")
async def stop_bot_confirmed(callback: CallbackQuery, state: FSMContext):
    """Подтверждение остановки бота — останавливает polling."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await safe_edit_or_send(callback.message, 
        "🛑 *Бот останавливается...*\n\n"
        "Спасибо за использование!",
        parse_mode="Markdown"
    )
    await callback.answer("Бот останавливается...", show_alert=True)
    
    logger.info(f"🛑 Бот остановлен администратором {callback.from_user.id}")
    
    # Даём время на отправку сообщения
    await asyncio.sleep(1)
    
    # Завершаем работу скрипта
    sys.exit(0)


# ============================================================================
# СКАЧИВАНИЕ ЛОГОВ
# ============================================================================

@router.callback_query(F.data == "admin_logs_menu")
async def show_logs_menu(callback: CallbackQuery, state: FSMContext):
    """Меню скачивания логов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
        
    await safe_edit_or_send(callback.message, 
        "📥 *Скачивание логов*\n\n"
        "Выберите какие логи хотите скачать:",
        reply_markup=admin_logs_menu_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_download_log_full")
async def download_log_full(callback: CallbackQuery, state: FSMContext):
    """Скачивание полного лога."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    log_path = "logs/bot.log"
    if not os.path.exists(log_path):
        await callback.answer("Файл логов не найден.", show_alert=True)
        return
    
    # Отвечаем на коллбек до отправки файла, чтобы избежать таймаута
    await callback.answer()
    
    await callback.message.answer_document(
        document=FSInputFile(log_path, filename="bot.log"),
        caption="📄 Полный лог бота"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_download_log_errors")
async def download_log_errors(callback: CallbackQuery, state: FSMContext):
    """Скачивание лога с ошибками."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    log_path = "logs/bot.log"
    error_log_path = "logs/errors.log"
    
    if not os.path.exists(log_path):
        await callback.answer("Файл логов не найден.", show_alert=True)
        return
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f_in, open(error_log_path, 'w', encoding='utf-8') as f_out:
            capturing = False
            for line in f_in:
                # Начало новой записи в логе формата [2026-...
                if line.startswith('['):
                    if ' [ERROR] ' in line or ' [WARNING] ' in line or ' [CRITICAL] ' in line or ' [EXCEPTION] ' in line:
                        capturing = True
                        f_out.write(line)
                    else:
                        capturing = False
                elif capturing:
                    # Строки traceback
                    f_out.write(line)
    except Exception as e:
        logger.error(f"Ошибка при формировании лога ошибок: {e}")
        await callback.answer("Ошибка при обработке логов.", show_alert=True)
        return
    
    if not os.path.exists(error_log_path) or os.path.getsize(error_log_path) == 0:
        await callback.answer("Ошибок не найдено! 🎉", show_alert=True)
        return
    
    # Отвечаем на коллбек до отправки файла, чтобы избежать таймаута
    await callback.answer()
        
    await callback.message.answer_document(
        document=FSInputFile(error_log_path, filename="errors.log"),
        caption="⚠️ Лог ошибок и предупреждений"
    )

@router.callback_query(F.data == "admin_clear_logs_confirm")
async def confirm_clear_logs(callback: CallbackQuery, state: FSMContext):
    """Показывает предупреждение перед очисткой логов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from bot.keyboards.admin import back_button
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Да, стереть", callback_data="admin_clear_logs_do"))
    builder.row(back_button("admin_logs_menu"))
    
    await safe_edit_or_send(callback.message,
        "🗑️ *Очистка логов*\n\n"
        "Вы уверены, что хотите полностью стереть файлы логов `bot.log` и `errors.log`?\n"
        "Это безвозвратное действие.",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_clear_logs_do")
async def do_clear_logs(callback: CallbackQuery, state: FSMContext):
    """Очищает файлы логов."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    try:
        log_path = "logs/bot.log"
        if os.path.exists(log_path):
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write("") 
        
        error_log_path = "logs/errors.log"
        if os.path.exists(error_log_path):
            with open(error_log_path, 'w', encoding='utf-8') as f:
                f.write("")
                
        await callback.answer("🗑️ Логи успешно очищены!", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка при очистке логов: {e}")
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    
    await show_logs_menu(callback, state)
