"""
Обработчики платежей пользователя.

Обрабатывает:
- Callback от криптопроцессинга (bill1-...)
- Оплату Telegram Stars
- Продление ключей
- Оплату с баланса
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext

from bot.utils.text import escape_md
from config import ADMIN_IDS

logger = logging.getLogger(__name__)

router = Router()


def _format_price_compact(cents: int) -> str:
    """Форматирование цены в компактном виде."""
    if cents >= 10000:
        return f"{cents // 100} ₽"
    else:
        return f"{cents / 100:.2f} ₽".replace(".", ",")


def _is_cards_via_yookassa_direct() -> bool:
    """
    Проверяет, используется ли оплата картами через ЮKassa напрямую (webhook).
    
    Returns:
        True если карты через ЮKassa напрямую (минимум 1₽),
        False если через Telegram Payments API (минимум ~100₽)
    """
    from database.requests import get_setting
    return get_setting('cards_via_yookassa_direct', '0') == '1'


async def _show_balance_payment_screen(
    callback: CallbackQuery,
    state: FSMContext,
    tariff_id: int,
    user_internal_id: int,
    key_id: int = None
):
    """
    Показать экран оплаты с учётом баланса по ТЗ.
    
    Вызывается по кнопке «💰 Использовать баланс».
    
    Расчёт:
        balance_to_deduct = min(balance, price)
        remaining_cents = price - balance_to_deduct
    
    Сохраняет в FSM state: balance_to_deduct, tariff_price_cents, tariff_id, key_id
    """
    from database.requests import (
        get_tariff_by_id, get_user_balance,
        is_cards_enabled, is_yookassa_qr_configured
    )
    from bot.keyboards.user import balance_payment_kb
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    tariff_price_cents = int(tariff.get('price_rub', 0) * 100)
    if tariff_price_cents <= 0:
        await callback.answer("❌ Ошибка: цена тарифа не задана", show_alert=True)
        return
    
    balance_cents = get_user_balance(user_internal_id)
    balance_to_deduct = min(balance_cents, tariff_price_cents)
    remaining_cents = max(0, tariff_price_cents - balance_to_deduct)
    
    await state.update_data(
        balance_to_deduct=balance_to_deduct,
        tariff_price_cents=tariff_price_cents,
        tariff_id=tariff_id,
        key_id=key_id
    )
    
    price_str = _format_price_compact(tariff_price_cents)
    balance_str = _format_price_compact(balance_cents)
    deduct_str = _format_price_compact(balance_to_deduct)
    remaining_str = _format_price_compact(remaining_cents)
    
    text = (
        f"💳 *Оплата тарифа «{escape_md(tariff['name'])}»*\n\n"
        f"💰 Сумма: {price_str}\n"
        f"💰 Ваш баланс: {balance_str}\n\n"
        f"✅ С баланса будет списано: {deduct_str}\n"
        f"💳 К оплате: {remaining_str}"
    )
    
    cards_enabled = is_cards_enabled()
    yookassa_qr_enabled = is_yookassa_qr_configured()
    cards_via_yookassa_direct = _is_cards_via_yookassa_direct()
    
    available_methods = []
    if yookassa_qr_enabled:
        available_methods.append('qr')
    if cards_enabled:
        if cards_via_yookassa_direct:
            available_methods.append('card')
        elif remaining_cents >= 10000:
            available_methods.append('card')
    
    if remaining_cents > 0 and not available_methods:
        text += (
            "\n\n💡 *Для доплаты этой суммы нет подходящего способа оплаты.*\n"
            "Поднакопите ещё немного на реферальном балансе\n"
            "или оплатите тариф без использования баланса."
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=balance_payment_kb(
            tariff_id=tariff_id,
            key_id=key_id,
            balance_cents=balance_cents,
            tariff_price_cents=tariff_price_cents,
            balance_to_deduct=balance_to_deduct,
            remaining_cents=remaining_cents,
            cards_enabled=cards_enabled,
            yookassa_qr_enabled=yookassa_qr_enabled,
            cards_via_yookassa_direct=cards_via_yookassa_direct
        ),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# КНОПКА «ИСПОЛЬЗОВАТЬ БАЛАНС» — ВЫБОР ТАРИФА
# ============================================================================

@router.callback_query(F.data == "pay_use_balance")
async def pay_use_balance_buy_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор тарифа для оплаты с баланса (новый ключ)."""
    from database.requests import (
        get_all_tariffs, get_user_internal_id,
        is_referral_enabled, get_referral_reward_type, get_user_balance
    )
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    
    telegram_id = callback.from_user.id
    user_id = get_user_internal_id(telegram_id)
    
    if not is_referral_enabled() or get_referral_reward_type() != 'balance':
        await callback.answer("❌ Оплата с баланса недоступна", show_alert=True)
        return
    
    balance_cents = get_user_balance(user_id) if user_id else 0
    if balance_cents <= 0:
        await callback.answer("❌ Недостаточно средств на балансе", show_alert=True)
        return
    
    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    
    if not rub_tariffs:
        await callback.message.edit_text(
            "💰 *Оплата с баланса*\n\n"
            "😔 Нет доступных тарифов с ценой в рублях.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        "💰 *Оплата с баланса*\n\n"
        f"Ваш баланс: *{_format_price_compact(balance_cents)}*\n\n"
        "Выберите тариф:",
        reply_markup=tariff_select_kb(rub_tariffs, back_callback="buy_key", is_balance=True),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_use_balance:"))
async def pay_use_balance_renew_handler(callback: CallbackQuery, state: FSMContext):
    """
    Обработка кнопки «Использовать баланс» для продления.
    Callback: pay_use_balance:{key_id}
    """
    from database.requests import (
        get_user_internal_id, get_key_details_for_user,
        is_referral_enabled, get_referral_reward_type, get_user_balance,
        get_all_tariffs
    )
    from bot.keyboards.user import renew_tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    
    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id
    user_id = get_user_internal_id(telegram_id)
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return
    
    if not is_referral_enabled() or get_referral_reward_type() != 'balance':
        await callback.answer("❌ Оплата с баланса недоступна", show_alert=True)
        return
    
    balance_cents = get_user_balance(user_id) if user_id else 0
    if balance_cents <= 0:
        await callback.answer("❌ Недостаточно средств на балансе", show_alert=True)
        return
    
    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    
    if not rub_tariffs:
        await callback.message.edit_text(
            "💰 *Оплата с баланса*\n\n"
            "😔 Нет доступных тарифов с ценой в рублях.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        f"💰 *Оплата с баланса*\n\n"
        f"🔑 Ключ: *{key['display_name']}*\n"
        f"Ваш баланс: *{_format_price_compact(balance_cents)}*\n\n"
        "Выберите тариф:",
        reply_markup=renew_tariff_select_kb(rub_tariffs, key_id, is_balance=True),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("balance_pay:"))
async def balance_pay_handler(callback: CallbackQuery, state: FSMContext):
    """
    Показ экрана оплаты с балансом после выбора тарифа.
    Callback: balance_pay:{tariff_id} или balance_pay:{tariff_id}:{key_id}
    """
    from database.requests import get_user_internal_id, get_tariff_by_id
    
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    key_id = int(parts[2]) if len(parts) > 2 and parts[2] != '0' else None
    
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Ошибка пользователя", show_alert=True)
        return
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    await _show_balance_payment_screen(callback, state, tariff_id, user_id, key_id=key_id)


# ============================================================================
# ОПЛАТА С БАЛАНСА (ПОЛНАЯ)
# ============================================================================

@router.callback_query(F.data.startswith("pay_with_balance:"))
async def pay_with_balance_handler(callback: CallbackQuery, state: FSMContext):
    """
    Полная оплата с баланса (когда remaining_cents == 0).
    Атомарная операция: списать + выдать ключ.
    
    При оплате балансом реферальные вознаграждения НЕ начисляются.
    """
    from database.requests import (
        get_user_internal_id, get_user_balance, deduct_from_balance,
        get_tariff_by_id, get_or_create_user, create_initial_vpn_key,
        extend_vpn_key
    )
    from bot.services.user_locks import user_locks
    from bot.services.vpn_api import reset_key_traffic_if_active, extend_key_on_server
    
    data = await state.get_data()
    balance_to_deduct = data.get('balance_to_deduct', 0)
    tariff_price_cents = data.get('tariff_price_cents', 0)
    tariff_id = data.get('tariff_id')
    key_id = data.get('key_id')
    
    parts = callback.data.split(":")
    if not tariff_id:
        tariff_id = int(parts[1]) if len(parts) > 1 else None
    if not key_id:
        key_id = int(parts[2]) if len(parts) > 2 and parts[2] else None
    
    if not tariff_id:
        await callback.answer("❌ Ошибка: тариф не определён", show_alert=True)
        return
    
    telegram_id = callback.from_user.id
    user, _ = get_or_create_user(telegram_id, callback.from_user.username)
    user_internal_id = user['id']
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    days = tariff['duration_days']
    
    async with user_locks[user_internal_id]:
        current_balance = get_user_balance(user_internal_id)
        
        if current_balance < tariff_price_cents:
            await callback.answer("❌ Недостаточно средств на балансе", show_alert=True)
            return
        
        actual_deduct = min(current_balance, tariff_price_cents)
        deduct_from_balance(user_internal_id, actual_deduct)
        
        if key_id:
            extend_vpn_key(key_id, days)
            await reset_key_traffic_if_active(key_id)
            await extend_key_on_server(key_id, days)
            logger.info(f"Ключ {key_id} продлён на {days} дней за баланс {actual_deduct} коп")
        else:
            create_initial_vpn_key(user_internal_id, tariff_id, days)
            logger.info(f"Создан черновик ключа для user {user_internal_id} за баланс {actual_deduct} коп")
    
    await state.update_data(balance_to_deduct=0)
    
    def format_price_compact(cents: int) -> str:
        if cents >= 10000:
            return f"{cents // 100} ₽"
        else:
            return f"{cents / 100:.2f} ₽".replace(".", ",")
    
    price_str = format_price_compact(actual_deduct)
    
    await callback.message.edit_text(
        f"✅ *Оплата успешно завершена!*\n\n"
        f"С вашего баланса списано {price_str}\n"
        f"Ключ активирован.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="🈴 На главную", callback_data="start")
        ).as_markup()
    )
    await callback.answer()


# ============================================================================
# ЧАСТИЧНАЯ ОПЛАТА С БАЛАНСОМ (ДОПЛАТА КАРТОЙ)
# ============================================================================

@router.callback_query(F.data.startswith("pay_card_balance:"))
async def pay_card_balance_handler(callback: CallbackQuery, state: FSMContext):
    """
    Частичная оплата: баланс + карта.
    
    Берёт данные из FSM state: balance_to_deduct, remaining_cents, tariff_id, key_id
    Создаёт инвойс на remaining_cents (не на полную цену тарифа!)
    """
    from aiogram.types import LabeledPrice
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, get_user_balance,
        create_pending_order, get_setting
    )
    from aiogram.exceptions import TelegramBadRequest
    
    data = await state.get_data()
    balance_to_deduct = data.get('balance_to_deduct', 0)
    tariff_price_cents = data.get('tariff_price_cents', 0)
    tariff_id = data.get('tariff_id')
    key_id = data.get('key_id')
    
    parts = callback.data.split(":")
    if not tariff_id:
        tariff_id = int(parts[1]) if len(parts) > 1 else None
    if not key_id:
        key_id = int(parts[2]) if len(parts) > 2 and parts[2] != '0' else None
    
    if not tariff_id:
        await callback.answer("❌ Ошибка: тариф не определён", show_alert=True)
        return
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await callback.answer("❌ Провайдер платежей не настроен", show_alert=True)
        return
    
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Ошибка пользователя", show_alert=True)
        return
    
    if not tariff_price_cents:
        tariff_price_cents = int(tariff.get('price_rub', 0) * 100)
    
    if not balance_to_deduct:
        balance_cents = get_user_balance(user_id)
        balance_to_deduct = min(balance_cents, tariff_price_cents)
    
    remaining_cents = tariff_price_cents - balance_to_deduct
    
    await state.update_data(
        balance_to_deduct=balance_to_deduct,
        tariff_price_cents=tariff_price_cents,
        tariff_id=tariff_id,
        key_id=key_id,
        remaining_cents=remaining_cents
    )
    
    _, order_id = create_pending_order(
        user_id=user_id,
        tariff_id=tariff_id,
        payment_type='cards',
        vpn_key_id=key_id
    )
    
    price_rub = remaining_cents / 100
    price_kopecks = remaining_cents
    
    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        
        back_cb = f"key_renew:{key_id}" if key_id else "buy_key"
        await callback.message.answer_invoice(
            title=bot_name,
            description=f"Оплата тарифа «{tariff['name']}» ({tariff['duration_days']} дн.).",
            payload=f"vpn_key:{order_id}",
            provider_token=provider_token,
            currency="RUB",
            prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)],
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text=f"💳 Оплатить {price_rub:.2f} ₽", pay=True)
            ).row(
                InlineKeyboardButton(text="❌ Отмена", callback_data=back_cb)
            ).as_markup()
        )
    except TelegramBadRequest as e:
        if "CURRENCY_TOTAL_AMOUNT_INVALID" in str(e):
            logger.warning(f"Ошибка платежа (CARDS): Неправильная сумма. Тариф: ID {tariff['id']}")
            await callback.answer("❌ Ошибка платежной системы. Сумма тарифа меньше допустимого лимита.", show_alert=True)
            return
        logger.exception("Ошибка при отправке инвойса картой.")
        raise e
    
    await callback.message.delete()
    await callback.answer()


# ============================================================================
# ЧАСТИЧНАЯ ОПЛАТА С БАЛАНСОМ (ДОПЛАТА QR)
# ============================================================================

@router.callback_query(F.data.startswith("pay_qr_balance:"))
async def pay_qr_balance_handler(callback: CallbackQuery, state: FSMContext):
    """
    Частичная оплата: баланс + QR (СБП).
    
    Берёт данные из FSM state: balance_to_deduct, remaining_cents, tariff_id, key_id
    Создаёт инвойс на remaining_cents / 100 рублей (ЮKassa принимает рубли)
    """
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, get_user_balance,
        create_pending_order, save_yookassa_payment_id
    )
    from bot.services.billing import create_yookassa_qr_payment
    from bot.keyboards.user import yookassa_qr_kb
    from bot.keyboards.admin import home_only_kb
    from aiogram.types import BufferedInputFile
    
    data = await state.get_data()
    balance_to_deduct = data.get('balance_to_deduct', 0)
    tariff_price_cents = data.get('tariff_price_cents', 0)
    tariff_id = data.get('tariff_id')
    key_id = data.get('key_id')
    
    parts = callback.data.split(":")
    if not tariff_id:
        tariff_id = int(parts[1]) if len(parts) > 1 else None
    if not key_id:
        key_id = int(parts[2]) if len(parts) > 2 and parts[2] != '0' else None
    
    if not tariff_id:
        await callback.answer("❌ Ошибка: тариф не определён", show_alert=True)
        return
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    if not tariff_price_cents:
        tariff_price_cents = int(tariff.get('price_rub', 0) * 100)
    
    if not balance_to_deduct:
        balance_cents = get_user_balance(user_id)
        balance_to_deduct = min(balance_cents, tariff_price_cents)
    
    remaining_cents = tariff_price_cents - balance_to_deduct
    remaining_rub = remaining_cents / 100
    
    await state.update_data(
        balance_to_deduct=balance_to_deduct,
        tariff_price_cents=tariff_price_cents,
        tariff_id=tariff_id,
        key_id=key_id,
        remaining_cents=remaining_cents
    )
    
    _, order_id = create_pending_order(
        user_id=user_id,
        tariff_id=tariff_id,
        payment_type='yookassa_qr',
        vpn_key_id=key_id
    )
    
    await callback.message.edit_text("⏳ Создаём QR-код для оплаты...")
    
    try:
        description = f"Покупка «{tariff['name']}» — {tariff['duration_days']} дней"
        result = await create_yookassa_qr_payment(
            amount_rub=remaining_rub,
            order_id=order_id,
            description=description
        )
        
        save_yookassa_payment_id(order_id, result['yookassa_payment_id'])
        
        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')
        if not qr_image_data or not qr_url:
            await callback.message.edit_text(
                "❌ ЮКасса не вернула данные для оплаты. Попробуйте позже.",
                reply_markup=home_only_kb(),
                parse_mode="Markdown"
            )
            return
        
        text = (
            f"📱 *QR-код для оплаты*\n\n"
            f"💳 *Тариф:* {tariff['name']}\n"
            f"💰 *Сумма:* {remaining_rub:.2f} ₽\n"
            f"⏳ *Срок:* {tariff['duration_days']} дней\n\n"
            f"Отсканируйте QR-код банковским приложением (СБП) или перейдите по [ссылке на оплату]({qr_url}).\n\n"
            "_После оплаты нажмите «✅ Я оплатил»._"
        )
        
        photo = BufferedInputFile(qr_image_data, filename="qr.png")
        
        back_cb = f"key_renew:{key_id}" if key_id else "buy_key"
        
        await callback.message.delete()
        await callback.message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=yookassa_qr_kb(order_id, back_callback=back_cb),
            parse_mode="Markdown"
        )
        
    except (ValueError, RuntimeError) as e:
        logger.error(f"Ошибка создания QR ЮКасса: {e}")
        await callback.message.edit_text(
            f"❌ *Ошибка создания QR*\n\n_{e}_\n\nПопробуйте другой способ оплаты.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
    
    await callback.answer()


# ============================================================================
# ПРОДЛЕНИЕ: ВЫБОР СПОСОБА ОПЛАТЫ (STARS)
# ============================================================================

@router.callback_query(F.data.startswith("renew_stars_tariff:"))
async def renew_stars_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для продления (Stars)."""
    from database.requests import get_key_details_for_user, get_all_tariffs
    from bot.keyboards.user import renew_tariff_select_kb
    
    parts = callback.data.split(':')
    key_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    
    telegram_id = callback.from_user.id
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    # Получаем тарифы
    tariffs = get_all_tariffs(include_hidden=False)
    
    if not tariffs:
         await callback.answer("Нет доступных тарифов", show_alert=True)
         return

    await callback.message.edit_text(
        f"⭐ *Оплата звёздами*\n\n"
        f"🔑 Ключ: *{escape_md(key['display_name'])}*\n\n"
        "Выберите тариф для продления:",
        reply_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ПРОДЛЕНИЕ: ВЫБОР СПОСОБА ОПЛАТЫ (CRYPTO - ПРОСТОЙ РЕЖИМ)
# ============================================================================

@router.callback_query(F.data.startswith("renew_crypto_tariff:"))
async def renew_crypto_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для продления (Crypto)."""
    from database.requests import get_key_details_for_user, get_all_tariffs
    from bot.keyboards.user import renew_tariff_select_kb
    
    parts = callback.data.split(':')
    key_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    
    telegram_id = callback.from_user.id
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    # Получаем тарифы
    tariffs = get_all_tariffs(include_hidden=False)
    
    if not tariffs:
         await callback.answer("Нет доступных тарифов", show_alert=True)
         return

    await callback.message.edit_text(
        f"💰 *Оплата криптовалютой*\n\n"
        f"🔑 Ключ: *{escape_md(key['display_name'])}*\n\n"
        "Выберите тариф для продления:",
        reply_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id, is_crypto=True),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ОПЛАТА STARS ЗА ПРОДЛЕНИЕ
# ============================================================================

@router.callback_query(F.data.startswith("renew_pay_stars:"))
async def renew_stars_invoice(callback: CallbackQuery):
    """Инвойс для продления (Stars)."""
    from aiogram.types import LabeledPrice
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, 
        create_pending_order, get_key_details_for_user,
        update_order_tariff, update_payment_type
    )
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    order_id = parts[3] if len(parts) > 3 else None
    
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    
    if not tariff or not key:
        await callback.answer("Ошибка тарифа или ключа", show_alert=True)
        return
        
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        return

    if order_id:
         update_order_tariff(order_id, tariff_id)
         update_payment_type(order_id, 'stars')
    else:
         _, order_id = create_pending_order(
            user_id=user_id,
            tariff_id=tariff_id,
            payment_type='stars',
            vpn_key_id=key_id
        )
    
    bot_info = await callback.bot.get_me()
    bot_name = bot_info.first_name
    
    await callback.message.answer_invoice(
        title=bot_name,
        description=f"Продление ключа «{key['display_name']}»: {tariff['name']}.",
        payload=f"renew:{order_id}",
        currency="XTR",
        prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=tariff['price_stars'])],
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text=f"⭐️ Оплатить {tariff['price_stars']} XTR", pay=True)
        ).row(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"renew_invoice_cancel:{key_id}:{tariff_id}")
        ).as_markup()
    )
    
    await callback.message.delete()
    await callback.answer()


# ============================================================================
# ОПЛАТА CRYPTO ЗА ПРОДЛЕНИЕ (ПРОСТОЙ РЕЖИМ)
# ============================================================================

@router.callback_query(F.data.startswith("renew_pay_crypto:"))
async def renew_crypto_invoice(callback: CallbackQuery):
    """Инвойс для оплаты Crypto (за продление ключа)."""
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, 
        create_pending_order, get_key_details_for_user,
        update_order_tariff, update_payment_type, get_setting
    )
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    order_id = parts[3] if len(parts) > 3 else None
    
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    
    if not tariff or not key:
        await callback.answer("Ошибка тарифа или ключа", show_alert=True)
        return
    
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        return

    # Логика создания/обновления ордера
    if order_id:
         # Переиспользуем существующий
         update_order_tariff(order_id, tariff_id)
         update_payment_type(order_id, 'crypto')
    else:
         # Создаем новый
         _, order_id = create_pending_order(
            user_id=user_id,
            tariff_id=tariff_id,
            payment_type='crypto',
            vpn_key_id=key_id
        )

    crypto_item_url = get_setting('crypto_item_url')
    item_id = extract_item_id_from_url(crypto_item_url)
    if not item_id:
        await callback.answer("❌ Ошибка настройки крипто-платежей", show_alert=True)
        return

    crypto_url = build_crypto_payment_url(
        item_id=item_id,
        invoice_id=order_id,
        tariff_external_id=None,
        price_cents=tariff['price_cents']
    )

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💰 Перейти к оплате", url=crypto_url))
    cb_data = f"renew_crypto_tariff:{key_id}:{order_id}" if order_id else f"renew_crypto_tariff:{key_id}"
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=cb_data))

    price_usd = tariff['price_cents'] / 100
    price_str = f"${price_usd:g}".replace('.', ',')

    await callback.message.edit_text(
        f"💰 *Продление ключа*\n\n"
        f"🔑 Ключ: *{escape_md(key['display_name'])}*\n"
        f"Тариф: *{tariff['name']}*\n"
        f"Сумма к оплате: *{price_str}*\n\n"
        "Нажмите кнопку ниже, чтобы перейти к генерации счета в @Ya\\_SellerBot.",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# ОБРАБОТКА TELEGRAM STARS
# ============================================================================

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    """Подтверждение pre-checkout для Telegram Stars."""
    # Всегда подтверждаем — проверки делаем при создании invoice
    await pre_checkout.answer(ok=True)



@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, state: FSMContext):
    """
    Обработка успешной оплаты Stars или Cards.
    
    При частичной оплате с балансом:
    - Списывает баланс под user_locks
    - process_referral_reward получает только внешнюю сумму (remaining_cents)
    - Сбрасывает balance_to_deduct в 0
    """
    from bot.services.billing import process_payment_order, process_referral_reward
    from database.requests import get_user_balance, deduct_from_balance
    from bot.services.user_locks import user_locks
    
    payment = message.successful_payment
    payload = payment.invoice_payload
    currency = payment.currency
    
    payment_type = 'stars' if currency == 'XTR' else 'cards'
    
    logger.info(f"Успешная оплата {payment_type}: {payload}, charge_id={payment.telegram_payment_charge_id}")
    
    data = await state.get_data()
    balance_to_deduct = data.get('balance_to_deduct', 0)
    remaining_cents = data.get('remaining_cents', 0)
    
    if payload.startswith("renew:"):
        order_id = payload.split(":")[1]
    elif payload.startswith("vpn_key:"):
        order_id = payload.split(":")[1]
    else:
        order_id = payload
    
    try:
        success, text, order = await process_payment_order(order_id)
        
        if success and order:
            user_internal_id = order['user_id']
            days = order.get('period_days') or order.get('duration_days') or 30
            
            if balance_to_deduct > 0:
                async with user_locks[user_internal_id]:
                    current_balance = get_user_balance(user_internal_id)
                    actual_deduct = min(balance_to_deduct, current_balance)
                    if actual_deduct > 0:
                        deduct_from_balance(user_internal_id, actual_deduct)
                        logger.info(f"Списано {actual_deduct} коп с баланса user {user_internal_id} при частичной оплате")
            
            await state.update_data(balance_to_deduct=0, remaining_cents=0)
            
            if payment_type == 'stars':
                amount = payment.total_amount
            else:
                amount = payment.total_amount
            
            await process_referral_reward(user_internal_id, days, amount, payment_type)
            
            await finalize_payment_ui(message, state, text, order, user_id=message.from_user.id)
        else:
             from bot.keyboards.admin import home_only_kb
             await message.answer(text, reply_markup=home_only_kb(), parse_mode="Markdown")
             
    except Exception as e:
        from bot.errors import TariffNotFoundError
        if isinstance(e, TariffNotFoundError):
            from database.requests import get_setting
            from bot.keyboards.user import support_kb
            
            support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
            await message.answer(
                str(e),
                reply_markup=support_kb(support_link),
                parse_mode="Markdown"
            )
        else:
            logger.exception(f"Ошибка обработки {payment_type} платежа: {e}")
            await message.answer("❌ Произошла ошибка при обработке платежа.", parse_mode="Markdown")


async def finalize_payment_ui(message: Message, state: FSMContext, text: str, order: dict, user_id: int):
    """
    Завершает UI после успешной оплаты.
    Показывает сообщение и либо перекидывает на настройку (draft), либо на главную.
    """
    from bot.keyboards.admin import home_only_kb
    from database.requests import get_key_details_for_user
    import logging
    
    # Локальный логгер, если глобальный недоступен
    logger = logging.getLogger(__name__)
    
    key_id = order.get('vpn_key_id')
    
    logger.info(f"finalize_payment_ui: Order={order.get('order_id')}, Key={key_id}, User={user_id}")
    
    is_draft = False
    if key_id:
        key = get_key_details_for_user(key_id, user_id)
        if key:
            logger.info(f"Key details found: ID={key['id']}, ServerID={key.get('server_id')}")
            # Если сервер не выбран - это черновик
            if not key.get('server_id'):
                is_draft = True
        else:
            logger.warning(f"Key {key_id} not found for user {user_id} via details check!")
    else:
        logger.info("No key_id in order object.")

    logger.info(f"Result: is_draft={is_draft}")

    logger.info(f"Result: is_draft={is_draft}")
            
    if is_draft:
        # Если это черновик - сначала поздравляем, потом сразу запускаем настройку
        await message.answer(text, parse_mode="Markdown")
        await start_new_key_config(message, state, order['order_id'], key_id)
    else:
        # Если это продление или готовый ключ
        from bot.handlers.user.main import show_key_details
        await show_key_details(
            telegram_id=user_id,
            key_id=key_id,
            send_function=message.answer,
            prepend_text=text
        )


async def start_new_key_config(message: Message, state: FSMContext, order_id: str, key_id: int = None):
    """
    Запускает процесс настройки нового ключа (выбор сервера).
    Используется как для Stars, так и для Crypto.
    """
    from database.requests import get_active_servers
    from bot.keyboards.user import new_key_server_list_kb
    from bot.keyboards.admin import home_only_kb
    from bot.states.user_states import NewKeyConfig
    
    servers = get_active_servers()
    
    if not servers:
        logger.error(f"Нет активных серверов для создания ключа (Order: {order_id})")
        await message.answer(
            "🎉 *Оплата прошла успешно!*\n\n"
            "⚠️ К сожалению, сейчас нет доступных серверов.\n"
            "Пожалуйста, свяжитесь с поддержкой.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        return

    # Устанавливаем состояние
    await state.set_state(NewKeyConfig.waiting_for_server)
    await state.update_data(new_key_order_id=order_id, new_key_id=key_id)
    
    await message.answer(
        "🎉 *Оплата прошла успешно!*\n\n"
        "🔑 Теперь выберите сервер для вашего нового ключа.",
        reply_markup=new_key_server_list_kb(servers),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("renew_invoice_cancel:"))
async def renew_invoice_cancel_handler(callback: CallbackQuery):
    """Отмена инвойса и возврат к выбору способа оплаты."""
    from bot.keyboards.user import renew_payment_method_kb
    from database.requests import get_key_details_for_user, get_all_tariffs, is_crypto_configured, is_stars_enabled, is_cards_enabled, get_user_internal_id, create_pending_order, get_setting
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    telegram_id = callback.from_user.id
    
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    crypto_configured = is_crypto_configured()
    stars_enabled = is_stars_enabled()
    cards_enabled = is_cards_enabled()
    
    if not crypto_configured and not stars_enabled and not cards_enabled:
         await callback.message.answer("😔 Способы оплаты временно недоступны.", parse_mode="Markdown")
         return
         
    crypto_url = None
    if crypto_configured:
        tariffs = get_all_tariffs(include_hidden=False)
        if tariffs:
            user_id = get_user_internal_id(telegram_id)
            if user_id:
                 _, order_id = create_pending_order(
                    user_id=user_id,
                    tariff_id=tariffs[0]['id'],
                    payment_type='crypto',
                    vpn_key_id=key_id
                )
                 item_url = get_setting('crypto_item_url')
                 item_id = extract_item_id_from_url(item_url)
                 if item_id:
                     crypto_url = build_crypto_payment_url(item_id=item_id, invoice_id=order_id, tariff_external_id=None, price_cents=None)

    await callback.message.answer(
        f"💳 *Продление ключа*\n\n"
        f"🔑 Ключ: *{key['display_name']}*\n\n"
        "Выберите способ оплаты:",
        reply_markup=renew_payment_method_kb(key_id, crypto_url, stars_enabled, cards_enabled),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============================================================================
# СОЗДАНИЕ НОВОГО КЛЮЧА (ПОСЛЕ ОПЛАТЫ)
# ============================================================================

@router.callback_query(F.data.startswith("new_key_server:"))
async def process_new_key_server_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор сервера для нового ключа."""
    from database.requests import get_server_by_id
    from bot.services.vpn_api import get_client, VPNAPIError
    from bot.keyboards.user import new_key_inbound_list_kb
    from bot.states.user_states import NewKeyConfig
    
    server_id = int(callback.data.split(":")[1])
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await state.update_data(new_key_server_id=server_id)
    
    try:
        client = await get_client(server_id)
        inbounds = await client.get_inbounds()
        
        if not inbounds:
            await callback.answer("❌ На сервере нет доступных протоколов", show_alert=True)
            return
        
        # Если inbound только один — выбираем автоматически
        if len(inbounds) == 1:
            await process_new_key_final(callback, state, server_id, inbounds[0]['id'])
            return

        await state.set_state(NewKeyConfig.waiting_for_inbound)
        
        await callback.message.edit_text(
            f"🖥️ *Сервер:* {server['name']}\n\n"
            "Выберите протокол:",
            reply_markup=new_key_inbound_list_kb(inbounds),
            parse_mode="Markdown"
        )
    except VPNAPIError as e:
        await callback.answer(f"❌ Ошибка подключения: {e}", show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("new_key_inbound:"))
async def process_new_key_inbound_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор протокола (inbound) для нового ключа."""
    inbound_id = int(callback.data.split(":")[1])
    
    data = await state.get_data()
    server_id = data.get('new_key_server_id')
    
    await process_new_key_final(callback, state, server_id, inbound_id)


async def process_new_key_final(callback: CallbackQuery, state: FSMContext, server_id: int, inbound_id: int):
    """Финальный этап создания ключа."""
    from database.requests import (
        get_server_by_id, update_vpn_key_config, update_payment_key_id, 
        find_order_by_order_id, get_user_internal_id,
        get_key_details_for_user, create_initial_vpn_key
    )
    from bot.services.vpn_api import get_client
    from bot.handlers.admin.users import generate_unique_email
    from bot.utils.key_sender import send_key_with_qr
    from bot.keyboards.user import key_issued_kb
    from config import DEFAULT_TOTAL_GB
    
    data = await state.get_data()
    order_id = data.get('new_key_order_id')
    key_id = data.get('new_key_id')
    
    if not order_id:
        await callback.message.edit_text("❌ Ошибка: потерян номер заказа.")
        await state.clear()
        return

    order = find_order_by_order_id(order_id)
    if not order:
        await callback.message.edit_text("❌ Ошибка: заказ не найден.")
        await state.clear()
        return
    
    # Если key_id не передан через state, ищем в ордере
    if not key_id:
        if order['vpn_key_id']:
            key_id = order['vpn_key_id']
        else:
            # Если ключа нет (экстренный случай), создаем
            days = order.get('period_days') or order.get('duration_days') or 30
            key_id = create_initial_vpn_key(order['user_id'], order['tariff_id'], days)
            update_payment_key_id(order_id, key_id)

    await callback.message.edit_text("⏳ Настраиваем ваш ключ...")
    
    try:
        user_id = order['user_id']
        telegram_id = callback.from_user.id
        username = callback.from_user.username
        
        # Данные для генерации email
        user_fake_dict = {'telegram_id': telegram_id, 'username': username}
        panel_email = generate_unique_email(user_fake_dict)
        
        client = await get_client(server_id)
        
        # Создаем ключ на сервере
        days = order.get('period_days') or order.get('duration_days') or 30
        
        # Конвертируем байты в ГБ (int) для API
        limit_gb = int(DEFAULT_TOTAL_GB / (1024**3))
        
        # Определяем flow для inbound (xtls-rprx-vision для VLESS Reality TCP)
        flow = await client.get_inbound_flow(inbound_id)
        
        res = await client.add_client(
            inbound_id=inbound_id,
            email=panel_email,
            total_gb=limit_gb, 
            expire_days=days,
            limit_ip=1,
            enable=True,
            tg_id=str(telegram_id),
            flow=flow
        )
        
        client_uuid = res['uuid']
        
        # Обновляем конфигурацию существующего ключа
        update_vpn_key_config(
            key_id=key_id,
            server_id=server_id,
            panel_inbound_id=inbound_id,
            panel_email=panel_email,
            client_uuid=client_uuid
        )
        
        # Привязываем ключ к платежу (повт.)
        update_payment_key_id(order_id, key_id)
        
        await state.clear()
        
        # Получаем данные ключа для отображения
        new_key = get_key_details_for_user(key_id, telegram_id)
        
        # Используем унифицированную отправку
        await send_key_with_qr(callback, new_key, key_issued_kb(), is_new=True)

    except Exception as e:
        logger.error(f"Ошибка настройки ключа (id={key_id}): {e}")
        await callback.message.edit_text(
            f"❌ Ошибка настройки ключа: {e}\n"
            "Обратитесь в поддержку, указав Order ID: " + str(order_id)
        )


@router.callback_query(F.data == "back_to_server_select")
async def back_to_server_select(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору сервера."""
    from database.requests import get_active_servers
    from bot.keyboards.user import new_key_server_list_kb
    from bot.states.user_states import NewKeyConfig
    
    servers = get_active_servers()
    await state.set_state(NewKeyConfig.waiting_for_server)
    
    await callback.message.edit_text(
        "🔑 Выберите сервер для вашего нового ключа.",
        reply_markup=new_key_server_list_kb(servers),
        parse_mode="Markdown"
    )


# ============================================================================
# ОПЛАТА КАРТАМИ ЮКАССА
# ============================================================================

@router.callback_query(F.data.startswith("pay_cards"))
async def pay_cards_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для оплаты Картой (Новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    
    order_id = None
    if ":" in callback.data:
        order_id = callback.data.split(":")[1]

    tariffs = get_all_tariffs(include_hidden=False)
    
    if not tariffs:
        await callback.message.edit_text(
            "💳 *Оплата картой*\n\n"
            "😔 Нет доступных тарифов.\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        "💳 *Оплата картой*\n\n"
        "Выберите тариф:",
        reply_markup=tariff_select_kb(tariffs, order_id=order_id, is_cards=True),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cards_pay:"))
async def pay_cards_invoice(callback: CallbackQuery):
    """Создание инвойса для оплаты Картой (Новый ключ)."""
    from aiogram.types import LabeledPrice
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, create_pending_order,
        update_order_tariff, get_setting
    )
    
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    user_id = get_user_internal_id(callback.from_user.id)
    
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await callback.answer("❌ Провайдер платежей не настроен", show_alert=True)
        return
        
    days = tariff['duration_days']
        
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='cards')
    else:
        if not user_id:
            await callback.answer("❌ Ошибка пользователя", show_alert=True)
            return

        _, order_id = create_pending_order(
            user_id=user_id,
            tariff_id=tariff_id,
            payment_type='cards',
            vpn_key_id=None 
        )

    price_rub = float(tariff.get('price_rub') or 0)
    price_kopecks = int(round(price_rub * 100))
    if price_kopecks <= 0:
        await callback.answer("❌ Ошибка: цена тарифа в рублях не задана.", show_alert=True)
        return
        
    from aiogram.exceptions import TelegramBadRequest

    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        
        await callback.message.answer_invoice(
            title=bot_name,
            description=f"Оплата тарифа «{tariff['name']}» ({days} дн.).",
            payload=f"vpn_key:{order_id}",
            provider_token=provider_token,
            currency="RUB",
            prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)],
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text=f"💳 Оплатить {price_rub} ₽", pay=True)
            ).row(
                InlineKeyboardButton(text="❌ Отмена", callback_data="buy_key")
            ).as_markup()
        )
    except TelegramBadRequest as e:
        if "CURRENCY_TOTAL_AMOUNT_INVALID" in str(e):
            logger.warning(f"Ошибка платежа (CARDS): Неправильная сумма (меньше лимита ~$1). Тариф: ID {tariff['id']}, Цена {price_rub} руб. Подробности: {e}")
            await callback.answer("❌ Ошибка платежной системы. К сожалению, сумма тарифа меньше допустимого лимита эквайринга.", show_alert=True)
            return
        logger.exception("Ошибка при отправке инвойса картой (новый ключ).")
        raise e
    
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data.startswith("renew_cards_tariff:"))
async def renew_cards_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для продления (Картой)."""
    from database.requests import get_key_details_for_user, get_all_tariffs
    from bot.keyboards.user import renew_tariff_select_kb
    
    parts = callback.data.split(':')
    key_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    
    telegram_id = callback.from_user.id
    
    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    tariffs = get_all_tariffs(include_hidden=False)
    
    if not tariffs:
         await callback.answer("Нет доступных тарифов", show_alert=True)
         return

    await callback.message.edit_text(
        f"💳 *Оплата картой*\n\n"
        f"🔑 Ключ: *{escape_md(key['display_name'])}*\n\n"
        "Выберите тариф для продления:",
        reply_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id, is_cards=True),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("renew_pay_cards:"))
async def renew_cards_invoice(callback: CallbackQuery):
    """Инвойс для продления (Картой)."""
    from aiogram.types import LabeledPrice
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, 
        create_pending_order, get_key_details_for_user,
        update_order_tariff, get_setting
    )
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    order_id = parts[3] if len(parts) > 3 else None
    
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    
    if not tariff or not key:
        await callback.answer("Ошибка тарифа или ключа", show_alert=True)
        return
    
    user_id = get_user_internal_id(callback.from_user.id)
    
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await callback.answer("❌ Провайдер платежей не настроен", show_alert=True)
        return
        
    if not user_id:
        return

    if order_id:
         update_order_tariff(order_id, tariff_id, payment_type='cards')
    else:
         _, order_id = create_pending_order(
            user_id=user_id,
            tariff_id=tariff_id,
            payment_type='cards',
            vpn_key_id=key_id
        )
    
    price_rub = float(tariff.get('price_rub') or 0)
    price_kopecks = int(round(price_rub * 100))
    if price_kopecks <= 0:
        await callback.answer("❌ Ошибка: цена тарифа в рублях не задана.", show_alert=True)
        return
        
    from aiogram.exceptions import TelegramBadRequest

    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        
        await callback.message.answer_invoice(
            title=bot_name,
            description=f"Продление ключа «{key['display_name']}»: {tariff['name']}.",
            payload=f"renew:{order_id}",
            provider_token=provider_token,
            currency="RUB",
            prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)],
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text=f"💳 Оплатить {tariff.get('price_rub', 0)} ₽", pay=True)
            ).row(
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"renew_invoice_cancel:{key_id}:{tariff_id}")
            ).as_markup()
        )
    except TelegramBadRequest as e:
        if "CURRENCY_TOTAL_AMOUNT_INVALID" in str(e):
            logger.warning(f"Ошибка платежа (CARDS_RENEW): Неправильная сумма (меньше лимита ~$1). Тариф: ID {tariff['id']}, Цена {price_rub} руб. Подробности: {e}")
            await callback.answer("❌ Ошибка платежной системы. К сожалению, сумма тарифа меньше допустимого лимита эквайринга.", show_alert=True)
            return
        logger.exception("Ошибка при отправке инвойса картой (продление ключа).")
        raise e
    
    await callback.message.delete()
    await callback.answer()


# ============================================================================
# QR-ОПЛАТА ЮКАССА (direct API — без Telegram Payments)
# ============================================================================

@router.callback_query(F.data == "pay_qr")
async def pay_qr_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для QR-оплаты (Новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import qr_tariff_select_kb
    from bot.keyboards.admin import home_only_kb

    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]

    if not rub_tariffs:
        await callback.message.edit_text(
            "📱 *QR-оплата*\n\n"
            "😔 Для QR-оплаты не настроены цены в рублях.\n"
            "Обратитесь к администратору.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "📱 *QR-оплата (Карта/СБП)*\n\n"
        "Выберите тариф:\n\n"
        "_Оплата через ЮКассу — поддерживает банковские карты и СБП._",
        reply_markup=qr_tariff_select_kb(rub_tariffs),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("qr_pay:"))
async def qr_pay_create(callback: CallbackQuery):
    """Создаёт QR-платёж ЮКасса для нового ключа и отправляет QR-фото."""
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, create_pending_order,
        save_yookassa_payment_id
    )
    from bot.services.billing import create_yookassa_qr_payment
    from bot.keyboards.user import yookassa_qr_kb
    from bot.keyboards.admin import home_only_kb

    tariff_id = int(callback.data.split(":")[1])
    tariff = get_tariff_by_id(tariff_id)

    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub <= 0:
        await callback.answer("❌ Цена в рублях не задана для этого тарифа", show_alert=True)
        return

    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    _, order_id = create_pending_order(
        user_id=user_id,
        tariff_id=tariff_id,
        payment_type='yookassa_qr',
        vpn_key_id=None
    )

    await callback.message.edit_text("⏳ Создаём QR-код для оплаты...")

    try:
        description = f"Покупка «{tariff['name']}» — {tariff['duration_days']} дней"
        result = await create_yookassa_qr_payment(
            amount_rub=price_rub,
            order_id=order_id,
            description=description
        )

        save_yookassa_payment_id(order_id, result['yookassa_payment_id'])

        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')
        if not qr_image_data or not qr_url:
            await callback.message.edit_text(
                "❌ ЮКасса не вернула данные для оплаты. Попробуйте позже.",
                reply_markup=home_only_kb(),
                parse_mode="Markdown"
            )
            return

        text = (
            f"📱 *QR-код для оплаты*\n\n"
            f"💳 *Тариф:* {tariff['name']}\n"
            f"💰 *Сумма:* {int(price_rub)} ₽\n"
            f"⏳ *Срок:* {tariff['duration_days']} дней\n\n"
            f"Отсканируйте QR-код банковским приложением (СБП) или перейдите по [ссылке на оплату]({qr_url}).\n\n"
            "_После оплаты нажмите «✅ Я оплатил»._"
        )
        
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(qr_image_data, filename="qr.png")

        await callback.message.delete()
        await callback.message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=yookassa_qr_kb(order_id, back_callback="pay_qr"),
            parse_mode="Markdown"
        )

    except (ValueError, RuntimeError) as e:
        logger.error(f"Ошибка создания QR ЮКасса: {e}")
        await callback.message.edit_text(
            f"❌ *Ошибка создания QR*\n\n_{e}_\n\nПопробуйте другой способ оплаты.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )

    await callback.answer()


@router.callback_query(F.data.startswith("check_yookassa_qr:"))
async def check_yookassa_payment(callback: CallbackQuery, state: FSMContext):
    """
    Проверяет статус QR-платежа ЮКасса по нажатию «✅ Я оплатил».
    При успехе — запускает процесс создания ключа.
    """
    from database.requests import (
        find_order_by_order_id, is_order_already_paid, update_payment_type,
        get_user_balance, deduct_from_balance
    )
    from bot.services.billing import check_yookassa_payment_status, process_payment_order, process_referral_reward
    from bot.keyboards.admin import home_only_kb
    from bot.services.user_locks import user_locks

    order_id = callback.data.split(":", 1)[1]

    if is_order_already_paid(order_id):
        order = find_order_by_order_id(order_id)
        if order:
            await finalize_payment_ui(callback.message, state,
                                      "✅ Оплата уже была обработана ранее.", order, user_id=callback.from_user.id)
        await callback.answer()
        return

    order = find_order_by_order_id(order_id)
    if not order:
        await callback.answer("❌ Ордер не найден", show_alert=True)
        return

    yookassa_payment_id = order.get('yookassa_payment_id')
    if not yookassa_payment_id:
        await callback.answer("⚠️ Нет данных о платеже. Попробуйте чуть позже.", show_alert=True)
        return

    await callback.answer("🔍 Проверяем платёж...")

    try:
        status = await check_yookassa_payment_status(yookassa_payment_id)
    except Exception as e:
        logger.error(f"Ошибка проверки статуса ЮКасса {yookassa_payment_id}: {e}")
        await callback.message.answer(
            "❌ Не удалось проверить статус платежа. Попробуйте позже.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
        return

    if status == 'succeeded':
        update_payment_type(order_id, 'yookassa_qr')

        state_data = await state.get_data()
        balance_to_deduct = state_data.get('balance_to_deduct', 0)
        remaining_cents = state_data.get('remaining_cents', 0)

        try:
            success, text, updated_order = await process_payment_order(order_id)
            if success and updated_order:
                user_internal_id = updated_order['user_id']
                days = updated_order.get('period_days') or updated_order.get('duration_days') or 30
                
                if balance_to_deduct > 0:
                    async with user_locks[user_internal_id]:
                        current_balance = get_user_balance(user_internal_id)
                        actual_deduct = min(balance_to_deduct, current_balance)
                        if actual_deduct > 0:
                            deduct_from_balance(user_internal_id, actual_deduct)
                            logger.info(f"Списано {actual_deduct} коп с баланса user {user_internal_id} при частичной QR-оплате")
                
                await state.update_data(balance_to_deduct=0, remaining_cents=0)
                
                await process_referral_reward(user_internal_id, days, remaining_cents, 'yookassa_qr')
                
                try:
                    await callback.message.delete()
                except Exception:
                    pass
                await finalize_payment_ui(callback.message, state, text, updated_order, user_id=callback.from_user.id)
            else:
                await callback.message.answer(
                    text, reply_markup=home_only_kb(), parse_mode="Markdown"
                )
        except Exception as e:
            from bot.errors import TariffNotFoundError
            if isinstance(e, TariffNotFoundError):
                from database.requests import get_setting
                from bot.keyboards.user import support_kb
                support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
                await callback.message.answer(str(e),
                                              reply_markup=support_kb(support_link),
                                              parse_mode="Markdown")
            else:
                logger.exception(f"Ошибка обработки QR-платежа: {e}")
                await callback.message.answer(
                    "❌ Произошла ошибка при обработке платежа.", parse_mode="Markdown"
                )

    elif status == 'canceled':
        await callback.message.answer(
            "❌ *Платёж отменён*\n\n"
            "Похоже, платёж был отменён или истёк срок QR-кода.\n"
            "Попробуйте снова выбрать тариф.",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )
    else:
        await callback.message.answer(
            "⏳ *Платёж ещё не поступил*\n\n"
            "Оплатите QR-код и нажмите «✅ Я оплатил» снова.\n\n"
            "_Если только что оплатили — подождите пару секунд._",
            parse_mode="Markdown"
        )


# ============================================================================
# QR-ОПЛАТА ЮКАССА ПРИ ПРОДЛЕНИИ КЛЮЧА
# ============================================================================

@router.callback_query(F.data.startswith("renew_qr_tariff:"))
async def renew_qr_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для QR-оплаты при продлении ключа."""
    from database.requests import get_key_details_for_user, get_all_tariffs
    from bot.keyboards.user import renew_yookassa_qr_tariff_kb
    from bot.utils.text import escape_md

    key_id = int(callback.data.split(":")[1])
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]

    if not rub_tariffs:
        await callback.answer("😔 Нет тарифов с ценой в рублях", show_alert=True)
        return

    await callback.message.edit_text(
        f"📱 *QR-оплата (Карта/СБП)*\n\n"
        f"🔑 Ключ: *{escape_md(key['display_name'])}*\n\n"
        "Выберите тариф для продления:",
        reply_markup=renew_yookassa_qr_tariff_kb(rub_tariffs, key_id),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_pay_qr:"))
async def renew_qr_create(callback: CallbackQuery):
    """Создаёт QR-платёж ЮКасса для продления ключа."""
    from database.requests import (
        get_tariff_by_id, get_user_internal_id, create_pending_order,
        save_yookassa_payment_id, get_key_details_for_user
    )
    from bot.services.billing import create_yookassa_qr_payment
    from bot.keyboards.user import yookassa_qr_kb
    from bot.keyboards.admin import home_only_kb
    from bot.utils.text import escape_md

    parts = callback.data.split(":")
    key_id = int(parts[1])
    tariff_id = int(parts[2])

    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)

    if not tariff or not key:
        await callback.answer("❌ Ошибка тарифа или ключа", show_alert=True)
        return

    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub <= 0:
        await callback.answer("❌ Цена в рублях не задана", show_alert=True)
        return

    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    _, order_id = create_pending_order(
        user_id=user_id,
        tariff_id=tariff_id,
        payment_type='yookassa_qr',
        vpn_key_id=key_id
    )

    await callback.message.edit_text("⏳ Создаём QR-код для оплаты...")

    try:
        description = (
            f"Продление Ключа «{key['display_name']}»: "
            f"«{tariff['name']}» ({tariff['duration_days']} дн.)"
        )
        result = await create_yookassa_qr_payment(
            amount_rub=price_rub,
            order_id=order_id,
            description=description
        )

        save_yookassa_payment_id(order_id, result['yookassa_payment_id'])

        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')
        if not qr_image_data or not qr_url:
            await callback.message.edit_text(
                "❌ ЮКасса не вернула данные для оплаты. Попробуйте позже.",
                reply_markup=home_only_kb(),
                parse_mode="Markdown"
            )
            return

        text = (
            f"📱 *QR-код для оплаты*\n\n"
            f"🔑 *Ключ:* {escape_md(key['display_name'])}\n"
            f"💳 *Тариф:* {tariff['name']}\n"
            f"💰 *Сумма:* {int(price_rub)} ₽\n"
            f"⏳ *Продление:* +{tariff['duration_days']} дней\n\n"
            f"Отсканируйте QR-код банковским приложением (СБП) или перейдите по [ссылке на оплату]({qr_url}).\n\n"
            "_После оплаты нажмите «✅ Я оплатил»._"
        )
        
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(qr_image_data, filename="qr.png")

        await callback.message.delete()
        await callback.message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=yookassa_qr_kb(order_id, back_callback=f"renew_qr_tariff:{key_id}"),
            parse_mode="Markdown"
        )

    except (ValueError, RuntimeError) as e:
        logger.error(f"Ошибка QR ЮКасса (продление): {e}")
        await callback.message.edit_text(
            f"❌ *Ошибка создания QR*\n\n_{e}_",
            reply_markup=home_only_kb(),
            parse_mode="Markdown"
        )

    await callback.answer()

