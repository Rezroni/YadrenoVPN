"""
Роутер раздела «Оплаты».

Обрабатывает:
- Главный экран оплат
- Toggle для Stars/Crypto
- Настройка крипто-платежей
- Редактирование крипто-настроек
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, LinkPreviewOptions
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import (
    get_setting,
    set_setting,
    is_crypto_enabled,
    is_stars_enabled,
    is_cards_enabled,
    is_yookassa_qr_enabled,
    get_crypto_integration_mode,
    set_crypto_integration_mode
)
from bot.states.admin_states import (
    AdminStates,
    CRYPTO_PARAMS,
    get_crypto_param_by_index,
    get_total_crypto_params
)
from bot.utils.admin import is_admin
from bot.keyboards.admin import (
    payments_menu_kb,
    crypto_setup_kb,
    crypto_setup_confirm_kb,
    edit_crypto_kb,
    crypto_management_kb,
    cards_management_kb,
    back_and_home_kb
)
from bot.utils.text import escape_markdown_url, escape_md, safe_edit_or_send

logger = logging.getLogger(__name__)

from bot.utils.text import safe_edit_or_send

router = Router()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================


def has_crypto_data() -> bool:
    """Проверяет, заполнены ли данные крипто-платежей в БД."""
    url = get_setting('crypto_item_url', '')
    secret = get_setting('crypto_secret_key', '')
    return bool(url and secret)


def parse_item_id_from_url(url: str) -> str:
    """
    Извлекает item_id из ссылки на товар Ya.Seller.
    
    Формат: https://t.me/Ya_SellerBot?start=item-{item_id}...
    """
    try:
        if '?start=item-' in url:
            start_part = url.split('?start=item-')[1]
            # item_id — это первая часть до следующего дефиса или конца строки
            item_id = start_part.split('-')[0]
            return item_id
        elif '?start=item0-' in url:
            # Тестовый режим
            start_part = url.split('?start=item0-')[1]
            item_id = start_part.split('-')[0]
            return item_id
    except Exception:
        pass
    return ""


# ============================================================================
# ГЛАВНЫЙ ЭКРАН ОПЛАТ
# ============================================================================

@router.callback_query(F.data == "admin_payments")
async def show_payments_menu(callback: CallbackQuery, state: FSMContext):
    """Показывает главный экран раздела оплат."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.payments_menu)

    stars = is_stars_enabled()
    crypto = is_crypto_enabled()
    cards = is_cards_enabled()
    qr = is_yookassa_qr_enabled()

    text = (
        "💳 *Настройки оплаты*\n\n"
        "Здесь можно включить/выключить способы оплаты и настроить их.\n\n"
    )

    if stars:
        text += "🟢 *Telegram Stars*\n"
    else:
        text += "⚪ *Telegram Stars*\n"

    if crypto:
        item_url = get_setting('crypto_item_url', '')
        if item_url:
            safe_url = escape_markdown_url(item_url)
            text += f"🟢 *Крипто (@Ya_SellerBot)*\n[{item_url}]({safe_url})\n"
        else:
            text += "🟢 *Крипто (@Ya_SellerBot)*\n"
    else:
        text += "⚪ *Крипто (@Ya_SellerBot)*\n"

    if cards:
        text += "🟢 *Оплата картами (ЮКасса Telegram Payments)*\n"
    else:
        text += "⚪ *Оплата картами (ЮКасса Telegram Payments)*\n"

    if qr:
        shop_id = get_setting('yookassa_shop_id', '')
        text += f"🟢 *QR-оплата (ЮКасса прямая/СБП)* | Shop ID: `{shop_id or '—'}`\n"
    else:
        text += "⚪ *QR-оплата (ЮКасса прямая/СБП)*\n"

    monthly_reset = get_setting('monthly_traffic_reset_enabled', '0') == '1'

    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=payments_menu_kb(stars, crypto, cards, qr, monthly_reset),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    await callback.answer()


# ============================================================================
# TOGGLE MONTHLY RESET
# ============================================================================

@router.callback_query(F.data == "admin_toggle_monthly_reset")
async def toggle_monthly_reset(callback: CallbackQuery, state: FSMContext):
    """Переключение автосброса трафика 1-го числа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = get_setting('monthly_traffic_reset_enabled', '0')
    new_val = '0' if current == '1' else '1'
    set_setting('monthly_traffic_reset_enabled', new_val)
    
    # Перерисовываем меню оплат
    await show_payments_menu(callback, state)


# ============================================================================
# TOGGLE STARS
# ============================================================================

@router.callback_query(F.data == "admin_payments_toggle_stars")
async def toggle_stars(callback: CallbackQuery, state: FSMContext):
    """Переключает Telegram Stars."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = is_stars_enabled()
    new_value = '0' if current else '1'
    set_setting('stars_enabled', new_value)
    
    status = "включены ⭐" if new_value == '1' else "выключены"
    await callback.answer(f"Telegram Stars {status}")
    
    # Обновляем экран
    await show_payments_menu(callback, state)


# ============================================================================
# TOGGLE CRYPTO
# ============================================================================

@router.callback_query(F.data == "admin_payments_toggle_crypto")
async def toggle_crypto(callback: CallbackQuery, state: FSMContext):
    """Открывает настройку или меню управления крипто-платежами."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем, есть ли данные в БД
    if has_crypto_data():
        # Если данные есть → меню управления
        await show_crypto_management_menu(callback, state)
    else:
        # Если данных нет → диалог настройки
        await start_crypto_setup(callback, state)


# ============================================================================
# НАСТРОЙКА КРИПТО-ПЛАТЕЖЕЙ
# ============================================================================

async def start_crypto_setup(callback: CallbackQuery, state: FSMContext):
    """Начинает диалог настройки крипто-платежей."""
    await state.set_state(AdminStates.crypto_setup_url)
    await state.update_data(crypto_data={}, crypto_step=1)
    
    # Получаем username бота для инструкции
    bot_username = callback.bot.my_username if hasattr(callback.bot, 'my_username') else "YOUR_BOT"
    callback_url = f"https://t.me/{bot_username}"
    
    mode = get_crypto_integration_mode()
    
    instructions = (
        "*Режим «Простой» (рекомендуется):*\n"
        "1️⃣ В @Ya\\_SellerBot выберите «Управление» → «Товары» → «Добавить»\n"
        "2️⃣ Выберите тип позиции: *Счет*\n\n"
        "🎬 *Актуальная инструкция как добавлять:*\n"
        "[Смотреть видео](https://youtu.be/cK0wX2LKxcs)\n\n"
        "⚠️ *ВАЖНО:*\n"
        "• Тип позиции — именно *Счет*, а НЕ *Товар*!\n"
        "• Тарифы добавлять к позиции *НЕ нужно* — в режиме «Счет» их нельзя туда добавить.\n"
        "• Бот сам сформирует сумму оплаты на основе выбранного тарифа.\n\n"
    ) if mode == 'simple' else (
        "*Режим «Стандартный»:*\n"
        "1️⃣ Создайте обычный *Товар* в @Ya\\_SellerBot\n"
        "2️⃣ Добавьте в него тарифы (под номерами 1-9)\n"
        "3️⃣ Обязательно добавьте ID тарифов (1-9) из бота Ya.Seller в каждый тариф нашего VPN-бота.\n\n"
        "🎬 Процесс добавления товара показан в [видео-инструкции](https://www.youtube.com/watch?v=MYRTzvIkbi0).\n\n"
    )

    text = (
        "💰 *Настройка крипто-платежей*\n\n"
        "Для приёма криптовалюты мы используем @Ya\\_SellerBot.\n\n"
        f"{instructions}"
        "🔗 *Теперь скопируйте ссылку на позицию из бота и отправьте её мне:*"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=crypto_setup_kb(1),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


@router.message(AdminStates.crypto_setup_url)
async def process_crypto_url(message: Message, state: FSMContext):
    """Обрабатывает ввод ссылки на товар."""
    from bot.utils.text import get_message_text_for_storage
    
    url = get_message_text_for_storage(message, 'plain')
    
    # Валидация
    param = get_crypto_param_by_index(0)
    if not param['validate'](url):
        await message.answer(
            f"❌ {param['error']}\n\nПопробуйте ещё раз:",
            parse_mode="Markdown"
        )
        return
    
    # Удаляем сообщение
    try:
        await message.delete()
    except:
        pass
    
    # Проверяем режим
    data = await state.get_data()
    edit_mode = data.get('edit_mode', False)
    
    if edit_mode:
        # Режим редактирования - сохраняем и возвращаемся в меню
        set_setting('crypto_item_url', url)
        await state.update_data(edit_mode=False)
        
        safe_url = escape_markdown_url(url)
        await message.answer(
            f"✅ Ссылка обновлена!\n[{url}]({safe_url})",
            parse_mode="Markdown",
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
        
        # Создаём фейковый callback для показа меню
        class FakeCallback:
            def __init__(self, msg, user):
                self.message = msg
                self.from_user = user
                self.bot = msg.bot
            async def answer(self, *args, **kwargs):
                pass
        
        fake = FakeCallback(message, message.from_user)
        await show_crypto_management_menu(fake, state)
    else:
        # Режим настройки - сохраняем во временные данные
        crypto_data = data.get('crypto_data', {})
        crypto_data['crypto_item_url'] = url
        await state.update_data(crypto_data=crypto_data, crypto_step=2)
        
        # Переходим к вводу секретного ключа
        await state.set_state(AdminStates.crypto_setup_secret)
        
        bot_username = message.bot.my_username if hasattr(message.bot, 'my_username') else "YOUR_BOT"
        callback_url = f"https://t.me/{bot_username}"

        safe_url = escape_markdown_url(url)
        await message.answer(
            f"✅ Ссылка принята!\n[{url}]({safe_url})\n\n"
            "🔔 *Настройка уведомлений:*\n"
            "В @Ya\\_SellerBot зайдите в настройки вашей созданной позиции → `Уведомления` → `Обратная ссылка` и укажите этот адрес:\n"
            f"`{callback_url}`\n\n"
            "🔑 *Ожидаю ввода секретного ключа:*\n"
            "Найти его можно в @Ya\\_SellerBot: `Профиль` → `Ключ подписи`.",
            reply_markup=crypto_setup_kb(2),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            parse_mode="Markdown"
        )


@router.message(AdminStates.crypto_setup_secret)
async def process_crypto_secret(message: Message, state: FSMContext):
    """Обрабатывает ввод секретного ключа."""
    from bot.utils.text import get_message_text_for_storage
    
    secret = get_message_text_for_storage(message, 'plain')
    
    # Валидация
    param = get_crypto_param_by_index(1)
    if not param['validate'](secret):
        await message.answer(
            f"❌ {param['error']}\n\nПопробуйте ещё раз:",
            parse_mode="Markdown"
        )
        return
    
    # Удаляем сообщение (там секретный ключ!)
    try:
        await message.delete()
    except:
        pass
    
    # Проверяем режим
    data = await state.get_data()
    edit_mode = data.get('edit_mode', False)
    
    if edit_mode:
        # Режим редактирования - сохраняем и возвращаемся в меню
        set_setting('crypto_secret_key', secret)
        await state.update_data(edit_mode=False)
        await message.answer("✅ Секретный ключ обновлён!")
        
        # Создаём фейковый callback для показа меню
        class FakeCallback:
            def __init__(self, msg, user):
                self.message = msg
                self.from_user = user
                self.bot = msg.bot
            async def answer(self, *args, **kwargs):
                pass
        
        fake = FakeCallback(message, message.from_user)
        await show_crypto_management_menu(fake, state)
    else:
        # Режим настройки - сохраняем во временные данные
        crypto_data = data.get('crypto_data', {})
        crypto_data['crypto_secret_key'] = secret
        await state.update_data(crypto_data=crypto_data)
        
        # Переходим к подтверждению
        await state.set_state(AdminStates.payments_menu)
        
        item_url = crypto_data.get('crypto_item_url', '')
        safe_url = escape_markdown_url(item_url)
        
        await message.answer(
            "✅ *Все данные введены!*\n\n"
            f"📦 Товар: [{item_url}]({safe_url})\n"
            f"🔐 Ключ: `{'•' * 16}`\n\n"
            "Сохранить и включить крипто-платежи?",
            reply_markup=crypto_setup_confirm_kb(),
            parse_mode="Markdown",
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )


@router.callback_query(F.data == "admin_crypto_setup_back")
async def crypto_setup_back(callback: CallbackQuery, state: FSMContext):
    """Возврат на предыдущий шаг настройки крипто."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    step = data.get('crypto_step', 1)
    
    if step <= 1:
        # Возврат к меню оплат
        await show_payments_menu(callback, state)
    else:
        # Возврат к вводу URL
        await state.set_state(AdminStates.crypto_setup_url)
        await state.update_data(crypto_step=1)
        await start_crypto_setup(callback, state)
    
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_setup_save")
async def crypto_setup_save(callback: CallbackQuery, state: FSMContext):
    """Сохраняет настройки крипто и включает их."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    crypto_data = data.get('crypto_data', {})
    
    if not crypto_data.get('crypto_item_url') or not crypto_data.get('crypto_secret_key'):
        await callback.answer("❌ Данные не полные", show_alert=True)
        return
    
    # Сохраняем
    set_setting('crypto_item_url', crypto_data['crypto_item_url'])
    set_setting('crypto_secret_key', crypto_data['crypto_secret_key'])
    set_setting('crypto_enabled', '1')
    
    await callback.answer("✅ Крипто-платежи включены!")
    
    await safe_edit_or_send(callback.message, 
        "✅ *Крипто-платежи настроены и включены!*\n\n"
        "Теперь пользователи смогут оплачивать криптовалютой.\n"
        "Не забудьте добавить тарифы с указанием ID тарифа (1-9)!",
        parse_mode="Markdown"
    )
    
    # Показываем меню оплат
    await show_payments_menu(callback, state)


# ============================================================================
# МЕНЮ УПРАВЛЕНИЯ КРИПТО-ПЛАТЕЖАМИ
# ============================================================================

async def show_crypto_management_menu(callback: CallbackQuery, state: FSMContext):
    """Показывает меню управления крипто-платежами."""
    await state.set_state(AdminStates.payments_menu)
    
    is_enabled = is_crypto_enabled()
    item_url = get_setting('crypto_item_url', '')
    mode = get_crypto_integration_mode()
    
    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = "включены" if is_enabled else "выключены"
    
    mode_title = "Простой (Счет)" if mode == 'simple' else "Стандартный (Товар)"
    
    mode_description = (
        "ℹ️ *В Простом (Счет) режиме* бот генерирует ссылку на оплату с указанием точной суммы в долларах (из настроек тарифа).\n\n"
        "⚠️ *ВАЖНО:* В Ya.Seller позиция обязательно должна иметь тип *«Счет»*, а НЕ *«Товар»*! Тарифы к позиции добавлять не нужно — бот сам указывает сумму. Настраивать ID тарифов (external\\_id) не требуется.\n\n"
    ) if mode == 'simple' else (
        "ℹ️ *В Стандартном режиме* бот отправляет покупателя на одну ссылку-товар, где он выбирает тариф. Вам нужно обязательно заполнить поле «ID тарифа из Ya.Seller» для каждого тарифа.\n\n"
    )
    
    if item_url:
        safe_url = escape_markdown_url(item_url)
        text = (
            "💰 *Управление крипто-платежами*\n\n"
            f"{status_emoji} Статус: *{status_text}*\n"
            f"📦 Ссылка/Товар: [{item_url}]({safe_url})\n"
            f"⚙️ Текущий режим: *{mode_title}*\n\n"
            f"{mode_description}"
            "Выберите действие:"
        )
    else:
        text = (
            "💰 *Управление крипто-платежами*\n\n"
            f"{status_emoji} Статус: *{status_text}*\n"
            "📦 Ссылка/Товар: —\n"
            f"⚙️ Текущий режим: *{mode_title}*\n\n"
            f"{mode_description}"
            "Выберите действие:"
        )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=crypto_management_kb(is_enabled, mode),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_mgmt_toggle_mode")
async def crypto_mgmt_toggle_mode(callback: CallbackQuery, state: FSMContext):
    """Переключает режим интеграции с криптой (simple/standard)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current_mode = get_crypto_integration_mode()
    new_mode = 'standard' if current_mode == 'simple' else 'simple'
    set_crypto_integration_mode(new_mode)
    
    await callback.answer(f"Режим переключен на: {new_mode}")
    # Обновляем меню
    await show_crypto_management_menu(callback, state)


@router.callback_query(F.data == "admin_crypto_mgmt_toggle")
async def crypto_mgmt_toggle(callback: CallbackQuery, state: FSMContext):
    """Включает/выключает крипто-платежи (без потери данных)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = is_crypto_enabled()
    new_value = '0' if current else '1'
    set_setting('crypto_enabled', new_value)
    
    status = "включены ✅" if new_value == '1' else "выключены"
    await callback.answer(f"Крипто-платежи {status}")
    
    # Обновляем меню
    await show_crypto_management_menu(callback, state)


@router.callback_query(F.data == "admin_crypto_mgmt_edit_url")
async def crypto_mgmt_edit_url(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование ссылки на товар."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.crypto_setup_url)
    await state.update_data(edit_mode=True)
    
    current_url = get_setting('crypto_item_url', '')
    
    bot_username = callback.bot.my_username if hasattr(callback.bot, 'my_username') else "YOUR_BOT"
    callback_url = f"https://t.me/{bot_username}"
    mode = get_crypto_integration_mode()
    
    instructions = (
        "*Режим «Простой» (Счет):*\n"
        "1️⃣ В @Ya\\_SellerBot выберите «Управление» → «Товары» → «Добавить»\n"
        "2️⃣ Выберите тип позиции: *Счет*\n\n"
        "🎬 *Актуальная инструкция как добавлять:*\n"
        "[Смотреть видео](https://youtu.be/cK0wX2LKxcs)\n\n"
        "⚠️ *ВАЖНО:*\n"
        "• Тип позиции — именно *Счет*, а НЕ *Товар*!\n"
        "• Тарифы добавлять к позиции *НЕ нужно* — в режиме «Счет» их нельзя туда добавить.\n"
        "• Бот сам сформирует сумму оплаты на основе выбранного тарифа.\n\n"
    ) if mode == 'simple' else (
        "*Режим «Стандартный» (Товар):*\n"
        "1️⃣ Создайте обычный *Товар* в @Ya\\_SellerBot\n"
        "2️⃣ Добавьте в него тарифы (под номерами 1-9)\n"
        "3️⃣ Обязательно добавьте ID тарифов (1-9) из бота Ya.Seller в каждый тариф нашего VPN-бота.\n\n"
        "🎬 Процесс добавления товара показан в [видео-инструкции](https://www.youtube.com/watch?v=MYRTzvIkbi0).\n\n"
    )
    
    if current_url:
        safe_url = escape_markdown_url(current_url)
        text = (
            "🔗 *Изменение ссылки*\n\n"
            f"{instructions}"
            f"Текущая ссылка: [{current_url}]({safe_url})\n\n"
            "🔗 *Введите новую ссылку из @Ya_SellerBot:*"
        )
    else:
        text = (
            "🔗 *Изменение ссылки*\n\n"
            f"{instructions}"
            "Текущая ссылка: —\n\n"
            "🔗 *Введите новую ссылку из @Ya_SellerBot:*"
        )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=back_and_home_kb("admin_crypto_management"),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_mgmt_edit_secret")
async def crypto_mgmt_edit_secret(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование секретного ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.crypto_setup_secret)
    await state.update_data(edit_mode=True)
    
    bot_username = callback.bot.my_username if hasattr(callback.bot, 'my_username') else "YOUR_BOT"
    callback_url = f"https://t.me/{bot_username}"

    text = (
        "🔐 *Изменение секретного ключа*\n\n"
        "🔔 *Настройка уведомлений:*\n"
        "В @Ya\\_SellerBot зайдите в настройки вашей созданной позиции → `Уведомления` → `Обратная ссылка` и укажите этот адрес:\n"
        f"`{callback_url}`\n\n"
        "🔑 *Ожидаю ввода нового секретного ключа:*\n"
        "Найти его можно в @Ya\\_SellerBot: `Профиль` → `Ключ подписи`."
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=back_and_home_kb("admin_crypto_management"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_management")
async def back_to_crypto_management(callback: CallbackQuery, state: FSMContext):
    """Возврат в меню управления крипто-платежами."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await show_crypto_management_menu(callback, state)


# ============================================================================
# РЕДАКТИРОВАНИЕ КРИПТО-НАСТРОЕК
# ============================================================================

@router.callback_query(F.data == "admin_payments_crypto_settings")
async def start_edit_crypto(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование крипто-настроек."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.edit_crypto)
    await state.update_data(edit_crypto_param=0)
    
    await show_crypto_edit_screen(callback, state, 0)


async def show_crypto_edit_screen(callback: CallbackQuery, state: FSMContext, param_index: int):
    """Показывает экран редактирования крипто-настройки."""
    param = get_crypto_param_by_index(param_index)
    total = get_total_crypto_params()
    
    current_value = get_setting(param['key'], '')
    
    # Маскируем секретный ключ
    if param['key'] == 'crypto_secret_key' and current_value:
        display_value = '•' * min(len(current_value), 16)
    else:
        display_value = current_value or '—'
    
    text = (
        f"⚙️ *Настройки крипто-платежей* ({param_index + 1}/{total})\n\n"
        f"📌 Параметр: *{param['label']}*\n"
        f"📝 Текущее значение: `{display_value}`\n\n"
        f"Введите новое значение или используйте кнопки навигации:\n"
        f"({param['hint']})"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=edit_crypto_kb(param_index, total),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_crypto_edit_prev")
async def crypto_edit_prev(callback: CallbackQuery, state: FSMContext):
    """Предыдущий параметр крипто-настроек."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    current = data.get('edit_crypto_param', 0)
    new_param = max(0, current - 1)
    await state.update_data(edit_crypto_param=new_param)
    
    await show_crypto_edit_screen(callback, state, new_param)
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_edit_next")
async def crypto_edit_next(callback: CallbackQuery, state: FSMContext):
    """Следующий параметр крипто-настроек."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    current = data.get('edit_crypto_param', 0)
    total = get_total_crypto_params()
    new_param = min(total - 1, current + 1)
    await state.update_data(edit_crypto_param=new_param)
    
    await show_crypto_edit_screen(callback, state, new_param)
    await callback.answer()


@router.message(AdminStates.edit_crypto)
async def edit_crypto_value(message: Message, state: FSMContext):
    """Обрабатывает ввод нового значения крипто-настройки."""
    if not is_admin(message.from_user.id):
        return
    
    from bot.utils.text import get_message_text_for_storage
    
    data = await state.get_data()
    param_index = data.get('edit_crypto_param', 0)
    
    param = get_crypto_param_by_index(param_index)
    value = get_message_text_for_storage(message, 'plain')
    
    # Валидация
    if not param['validate'](value):
        await message.answer(
            f"❌ {param['error']}",
            parse_mode="Markdown"
        )
        return
    
    # Сохраняем в БД
    set_setting(param['key'], value)
    
    # Удаляем сообщение
    try:
        await message.delete()
    except:
        pass
    
    # Показываем обновлённый экран
    await message.answer(
        f"✅ *{param['label']}* обновлено!",
        parse_mode="Markdown"
    )
    
    # Создаём фейковый callback для показа экрана
    # Это хак, но работает
    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        
        async def answer(self, *args, **kwargs):
            pass
    
    fake = FakeCallback(message, message.from_user)
    await show_crypto_edit_screen(fake, state, param_index)


@router.callback_query(F.data == "admin_crypto_edit_done")
async def crypto_edit_done(callback: CallbackQuery, state: FSMContext):
    """Завершение редактирования крипто-настроек."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await callback.answer("✅ Настройки сохранены")
    await show_payments_menu(callback, state)


# ============================================================================
# УПРАВЛЕНИЕ ОПЛАТОЙ КАРТАМИ
# ============================================================================

@router.callback_query(F.data == "admin_payments_cards")
async def show_cards_management_menu(callback: CallbackQuery, state: FSMContext):
    """Показывает меню управления оплатой картами (ЮКасса)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)
    
    is_enabled = is_cards_enabled()
    token = get_setting('cards_provider_token', '')
    
    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = "включено" if is_enabled else "выключено"
    
    if token:
        # Маскируем токен: первые 4 и последние 4 символа
        masked_token = f"{token[:4]}...{token[-4:]}"
        token_display = f"Установлен ✅ (`{masked_token}`)"
    else:
        token_display = "Не установлен ❌"
    
    text = (
        "💳 *Управление оплатой картами*\n\n"
        "Для работы этого способа необходимо настроить провайдера ЮКасса.\n\n"
        "❗️ *ШАГ 1: РЕГИСТРАЦИЯ*\n"
        "Обязательно [зарегистрируйте магазин в ЮКассе по этой ссылке](https://yookassa.ru/joinups/?source=sva)\n\n"
        "После проверки документов ЮКассой переходите к настройке токена.\n\n"
        f"{status_emoji} Статус: *{status_text}*\n"
        f"🔑 Provider Token: *{token_display}*\n\n"
        "Выберите действие:"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=cards_management_kb(is_enabled),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    await callback.answer()


@router.callback_query(F.data == "admin_cards_mgmt_toggle")
async def cards_mgmt_toggle(callback: CallbackQuery, state: FSMContext):
    """Включает/выключает оплату картами."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Нельзя включить, если нет токена
    if not is_cards_enabled() and not get_setting('cards_provider_token', ''):
        await callback.answer("❌ Сначала укажите Provider Token!", show_alert=True)
        return

    current = is_cards_enabled()
    new_value = '0' if current else '1'
    set_setting('cards_enabled', new_value)
    
    status = "включена ✅" if new_value == '1' else "выключена"
    await callback.answer(f"Оплата картами {status}")
    
    # Обновляем меню
    await show_cards_management_menu(callback, state)


@router.callback_query(F.data == "admin_cards_mgmt_edit_token")
async def cards_mgmt_edit_token(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование токена ЮКасса."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.cards_setup_token)
    # Сохраняем ID сообщения, чтобы потом его отредактировать
    await state.update_data(last_menu_msg_id=callback.message.message_id)
    
    text = (
        "🔗 *Установка Provider Token*\n\n"
        "❗️ *ШАГ 1: РЕГИСТРАЦИЯ В ЮКАССЕ*\n"
        "Обязательно [зарегистрируйтесь по этой ссылке](https://yookassa.ru/joinups/?source=sva)\n\n"
        "*ШАГ 2: ПОЛУЧЕНИЕ ТОКЕНА В @BotFather*\n"
        "1. Отправьте команду `/mybots` и выберите бота.\n"
        "2. Нажмите `Payments` → `YooKassa`.\n"
        "3. Подключите магазин в боте провайдера и **обязательно вернитесь в @BotFather**.\n"
        "4. В BotFather снова откройте `Payments`, там появится токен.\n\n"
        "Отправьте полученный токен ответом на это сообщение:"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=back_and_home_kb("admin_payments_cards"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.cards_setup_token)
async def cards_setup_token_value(message: Message, state: FSMContext):
    """Обрабатывает ввод токена ЮКасса."""
    from bot.utils.text import get_message_text_for_storage
    
    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    token = get_message_text_for_storage(message, 'plain')
    
    if len(token) < 20 or ':' not in token:
        await message.answer("❌ Неверный формат токена. Попробуйте ещё раз:")
        return
    
    set_setting('cards_provider_token', token)
    
    try:
        await message.delete()
    except:
        pass
    
    # Если у нас есть ID сообщения меню, используем его для редактирования
    menu_message = message
    if last_menu_msg_id:
        try:
            # Создаем объект сообщения с нужным ID для редактирования
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛ Сохранение..."
            )
        except Exception:
            # Если не вышло (например, сообщение удалено), будем отвечать новым
            menu_message = await message.answer("⌛ Сохранение...")

    # Возвращаемся в меню через FakeCallback
    class FakeCallback:
        def __init__(self, msg, user, success_msg=None):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
            self.data = "admin_payments_cards"
            self.success_msg = success_msg
        
        async def answer(self, text=None, show_alert=False, *args, **kwargs):
            # Если передали текст для popup, запоминаем его (он будет показан при нажатии кнопок)
            # Но так как в AIOGram answerCallbackQuery работает только для реальных инстансов,
            # мы просто выведем информацию в консоль или пропустим.
            # Для пользователя мы добавим текст в само сообщение.
            pass

    fake = FakeCallback(menu_message, message.from_user)
    await show_cards_management_menu(fake, state)


# ============================================================================
# НАСТРОЙКА QR-ОПЛАТЫ ЮКАССА (прямой API)
# ============================================================================

def qr_management_kb(is_enabled: bool) -> "InlineKeyboardMarkup":
    """Клавиатура управления QR-оплатой ЮКасса."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    from bot.keyboards.admin import back_button, home_button

    builder = InlineKeyboardBuilder()

    toggle_text = "🔴 Выключить" if is_enabled else "🟢 Включить"
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data="admin_qr_mgmt_toggle"))
    builder.row(InlineKeyboardButton(text="🏪 Изменить Shop ID", callback_data="admin_qr_edit_shop_id"))
    builder.row(InlineKeyboardButton(text="🔐 Изменить Secret Key", callback_data="admin_qr_edit_secret"))
    builder.row(back_button("admin_payments"), home_button())

    return builder.as_markup()


@router.callback_query(F.data == "admin_payments_qr")
async def show_qr_management_menu(callback: CallbackQuery, state: FSMContext):
    """Показывает меню управления QR-оплатой ЮКасса."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)

    from database.requests import is_yookassa_qr_enabled
    is_enabled = is_yookassa_qr_enabled()
    shop_id = get_setting('yookassa_shop_id', '')
    secret_key = get_setting('yookassa_secret_key', '')

    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = "включено" if is_enabled else "выключено"

    shop_display = f"`{shop_id}`" if shop_id else "❌ Не задан"
    secret_display = f"Установлен ✅ (`{secret_key[:4]}...{secret_key[-4:]}`)" if len(secret_key) >= 8 else "❌ Не задан"

    text = (
        "📱 *QR-оплата ЮКасса (прямой API)*\n\n"
        "Позволяет принимать оплату картами и через СБП по QR-коду,\n"
        "без Telegram Payments.\n\n"
        "📋 *Как получить доступ:*\n"
        "1. Зарегистрируйте магазин: [yookassa.ru](https://yookassa.ru/joinups/?source=sva)\n"
        "2. Перейдите: Настройки → API-интеграция\n"
        "3. Скопируйте Shop ID и сгенерируйте новый Secret Key\n\n"
        f"{status_emoji} Статус: *{status_text}*\n"
        f"🏪 Shop ID: {shop_display}\n"
        f"🔑 Secret Key: {secret_display}\n\n"
        "Выберите действие:"
    )

    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=qr_management_kb(is_enabled),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    await callback.answer()


@router.callback_query(F.data == "admin_qr_mgmt_toggle")
async def qr_mgmt_toggle(callback: CallbackQuery, state: FSMContext):
    """Включает/выключает QR-оплату ЮКасса."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    from database.requests import is_yookassa_qr_enabled

    # Нельзя включить без реквизитов
    if not is_yookassa_qr_enabled():
        shop_id = get_setting('yookassa_shop_id', '')
        secret_key = get_setting('yookassa_secret_key', '')
        if not shop_id or not secret_key:
            await callback.answer("❌ Сначала укажите Shop ID и Secret Key!", show_alert=True)
            return

    current = is_yookassa_qr_enabled()
    new_value = '0' if current else '1'
    set_setting('yookassa_qr_enabled', new_value)

    status = "включена ✅" if new_value == '1' else "выключена"
    await callback.answer(f"QR-оплата {status}")
    await show_qr_management_menu(callback, state)


@router.callback_query(F.data == "admin_qr_edit_shop_id")
async def qr_edit_shop_id(callback: CallbackQuery, state: FSMContext):
    """Запрашивает Shop ID ЮКасса."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.qr_setup_shop_id)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    current = get_setting('yookassa_shop_id', '')
    current_display = f"\nТекущий: `{current}`" if current else ""

    await safe_edit_or_send(callback.message, 
        f"🏪 *Введите Shop ID ЮКасса*{current_display}\n\n"
        "Найдите в разделе: *Настройки → API-интеграция* вашего магазина.\n"
        "(Это числовой ID, например: `123456`)",
        reply_markup=back_and_home_kb("admin_payments_qr"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.qr_setup_shop_id)
async def qr_setup_shop_id_handler(message: Message, state: FSMContext):
    """Обрабатывает ввод Shop ID."""
    from bot.utils.text import get_message_text_for_storage
    
    shop_id = get_message_text_for_storage(message, 'plain')

    if not shop_id.isdigit() or len(shop_id) < 3:
        await message.answer("❌ Некорректный Shop ID. Должен быть числом (например, `123456`).",
                             parse_mode="Markdown")
        return

    try:
        await message.delete()
    except Exception:
        pass

    set_setting('yookassa_shop_id', shop_id)

    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        async def answer(self, *args, **kwargs):
            pass

    menu_message = message
    if last_menu_msg_id:
        try:
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛"
            )
        except Exception:
            menu_message = await message.answer("⌛")

    fake = FakeCallback(menu_message, message.from_user)
    await show_qr_management_menu(fake, state)



@router.callback_query(F.data == "admin_qr_edit_secret")
async def qr_edit_secret(callback: CallbackQuery, state: FSMContext):
    """Запрашивает Secret Key ЮКасса."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.qr_setup_secret_key)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(callback.message, 
        "🔐 *Введите Secret Key ЮКасса*\n\n"
        "Найдите в разделе: *Настройки → API-интеграция* вашего магазина.\n"
        "_(Секретный ключ будет скрыт после сохранения)_",
        reply_markup=back_and_home_kb("admin_payments_qr"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AdminStates.qr_setup_secret_key)
async def qr_setup_secret_key_handler(message: Message, state: FSMContext):
    """Обрабатывает ввод Secret Key."""
    from bot.utils.text import get_message_text_for_storage
    
    secret_key = get_message_text_for_storage(message, 'plain')

    if len(secret_key) < 16:
        await message.answer("❌ Слишком короткий ключ. Попробуйте ещё раз.")
        return

    try:
        await message.delete()
    except Exception:
        pass

    set_setting('yookassa_secret_key', secret_key)

    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        async def answer(self, *args, **kwargs):
            pass

    menu_message = message
    if last_menu_msg_id:
        try:
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛"
            )
        except Exception:
            menu_message = await message.answer("⌛")

    fake = FakeCallback(menu_message, message.from_user)
    await show_qr_management_menu(fake, state)


