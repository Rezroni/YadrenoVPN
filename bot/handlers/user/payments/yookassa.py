import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.text import escape_md, safe_edit_or_send
from config import ADMIN_IDS
from bot.handlers.user.payments.base import finalize_payment_ui

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(F.data.startswith('pay_cards'))
async def pay_cards_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для оплаты Картой (Новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    order_id = None
    if ':' in callback.data:
        order_id = callback.data.split(':')[1]
    tariffs = get_all_tariffs(include_hidden=False)
    if not tariffs:
        await safe_edit_or_send(callback.message, '💳 *Оплата картой*\n\n😔 Нет доступных тарифов.\n\nПопробуйте позже или обратитесь в поддержку.', reply_markup=home_only_kb(), parse_mode='Markdown')
        await callback.answer()
        return
    await safe_edit_or_send(callback.message, '💳 *Оплата картой*\n\nВыберите тариф:', reply_markup=tariff_select_kb(tariffs, order_id=order_id, is_cards=True), parse_mode='Markdown')
    await callback.answer()

@router.callback_query(F.data.startswith('cards_pay:'))
async def pay_cards_invoice(callback: CallbackQuery):
    """Создание инвойса для оплаты Картой (Новый ключ)."""
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, get_user_internal_id, create_pending_order, update_order_tariff, get_setting
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    order_id = parts[2] if len(parts) > 2 else None
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    user_id = get_user_internal_id(callback.from_user.id)
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await callback.answer('❌ Провайдер платежей не настроен', show_alert=True)
        return
    days = tariff['duration_days']
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='cards')
    else:
        if not user_id:
            await callback.answer('❌ Ошибка пользователя', show_alert=True)
            return
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='cards', vpn_key_id=None)
    price_rub = float(tariff.get('price_rub') or 0)
    price_kopecks = int(round(price_rub * 100))
    if price_kopecks <= 0:
        await callback.answer('❌ Ошибка: цена тарифа в рублях не задана.', show_alert=True)
        return
    from aiogram.exceptions import TelegramBadRequest
    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        await callback.message.answer_invoice(title=bot_name, description=f"Оплата тарифа «{tariff['name']}» ({days} дн.).", payload=f'vpn_key:{order_id}', provider_token=provider_token, currency='RUB', prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)], reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text=f'💳 Оплатить {price_rub} ₽', pay=True)).row(InlineKeyboardButton(text='❌ Отмена', callback_data='buy_key')).as_markup())
    except TelegramBadRequest as e:
        if 'CURRENCY_TOTAL_AMOUNT_INVALID' in str(e):
            logger.warning(f"Ошибка платежа (CARDS): Неправильная сумма (меньше лимита ~$1). Тариф: ID {tariff['id']}, Цена {price_rub} руб. Подробности: {e}")
            await callback.answer('❌ Ошибка платежной системы. К сожалению, сумма тарифа меньше допустимого лимита эквайринга.', show_alert=True)
            return
        logger.exception('Ошибка при отправке инвойса картой (новый ключ).')
        raise e
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data.startswith('renew_cards_tariff:'))
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
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
    from bot.utils.groups import get_tariffs_for_renewal
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    if not tariffs:
        await callback.answer('Нет доступных тарифов', show_alert=True)
        return
    await safe_edit_or_send(callback.message, f"💳 *Оплата картой*\n\n🔑 Ключ: *{escape_md(key['display_name'])}*\n\nВыберите тариф для продления:", reply_markup=renew_tariff_select_kb(tariffs, key_id, order_id=order_id, is_cards=True), parse_mode='Markdown')
    await callback.answer()

@router.callback_query(F.data.startswith('renew_pay_cards:'))
async def renew_cards_invoice(callback: CallbackQuery):
    """Инвойс для продления (Картой)."""
    from aiogram.types import LabeledPrice
    from database.requests import get_tariff_by_id, get_user_internal_id, create_pending_order, get_key_details_for_user, update_order_tariff, get_setting
    parts = callback.data.split(':')
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    order_id = parts[3] if len(parts) > 3 else None
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await callback.answer('Ошибка тарифа или ключа', show_alert=True)
        return
    user_id = get_user_internal_id(callback.from_user.id)
    provider_token = get_setting('cards_provider_token', '')
    if not provider_token:
        await callback.answer('❌ Провайдер платежей не настроен', show_alert=True)
        return
    if not user_id:
        return
    if order_id:
        update_order_tariff(order_id, tariff_id, payment_type='cards')
    else:
        (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='cards', vpn_key_id=key_id)
    price_rub = float(tariff.get('price_rub') or 0)
    price_kopecks = int(round(price_rub * 100))
    if price_kopecks <= 0:
        await callback.answer('❌ Ошибка: цена тарифа в рублях не задана.', show_alert=True)
        return
    from aiogram.exceptions import TelegramBadRequest
    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.first_name
        await callback.message.answer_invoice(title=bot_name, description=f"Продление ключа «{key['display_name']}»: {tariff['name']}.", payload=f'renew:{order_id}', provider_token=provider_token, currency='RUB', prices=[LabeledPrice(label=f"Тариф {tariff['name']}", amount=price_kopecks)], reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text=f"💳 Оплатить {tariff.get('price_rub', 0)} ₽", pay=True)).row(InlineKeyboardButton(text='❌ Отмена', callback_data=f'renew_invoice_cancel:{key_id}:{tariff_id}')).as_markup())
    except TelegramBadRequest as e:
        if 'CURRENCY_TOTAL_AMOUNT_INVALID' in str(e):
            logger.warning(f"Ошибка платежа (CARDS_RENEW): Неправильная сумма (меньше лимита ~$1). Тариф: ID {tariff['id']}, Цена {price_rub} руб. Подробности: {e}")
            await callback.answer('❌ Ошибка платежной системы. К сожалению, сумма тарифа меньше допустимого лимита эквайринга.', show_alert=True)
            return
        logger.exception('Ошибка при отправке инвойса картой (продление ключа).')
        raise e
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data == 'pay_qr')
async def pay_qr_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для QR-оплаты (Новый ключ)."""
    from database.requests import get_all_tariffs
    from bot.keyboards.user import tariff_select_kb
    from bot.keyboards.admin import home_only_kb
    tariffs = get_all_tariffs(include_hidden=False)
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    if not rub_tariffs:
        await safe_edit_or_send(callback.message, '📱 *QR-оплата*\n\n😔 Для QR-оплаты не настроены цены в рублях.\nОбратитесь к администратору.', reply_markup=home_only_kb(), parse_mode='Markdown')
        await callback.answer()
        return
    await safe_edit_or_send(callback.message, '📱 *QR-оплата (Карта/СБП)*\n\nВыберите тариф:\n\n_Оплата через ЮКассу — поддерживает банковские карты и СБП._', reply_markup=tariff_select_kb(rub_tariffs, is_qr=True), parse_mode='Markdown')
    await callback.answer()

@router.callback_query(F.data.startswith('qr_pay:'))
async def qr_pay_create(callback: CallbackQuery):
    """Создаёт QR-платёж ЮКасса для нового ключа и отправляет QR-фото."""
    from database.requests import get_tariff_by_id, get_user_internal_id, create_pending_order, save_yookassa_payment_id
    from bot.services.billing import create_yookassa_qr_payment
    from bot.keyboards.user import yookassa_qr_kb
    from bot.keyboards.admin import home_only_kb
    tariff_id = int(callback.data.split(':')[1])
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub <= 0:
        await callback.answer('❌ Цена в рублях не задана для этого тарифа', show_alert=True)
        return
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Пользователь не найден', show_alert=True)
        return
    (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='yookassa_qr', vpn_key_id=None)
    await safe_edit_or_send(callback.message, '⏳ Создаём QR-код для оплаты...')
    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.username
        description = f"Покупка «{tariff['name']}» — {tariff['duration_days']} дней"
        result = await create_yookassa_qr_payment(amount_rub=price_rub, order_id=order_id, description=description, bot_name=bot_name)
        save_yookassa_payment_id(order_id, result['yookassa_payment_id'])
        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')
        if not qr_image_data or not qr_url:
            await safe_edit_or_send(callback.message, '❌ ЮКасса не вернула данные для оплаты. Попробуйте позже.', reply_markup=home_only_kb(), parse_mode='Markdown')
            return
        text = f"📱 *QR-код для оплаты*\n\n💳 *Тариф:* {tariff['name']}\n💰 *Сумма:* {int(price_rub)} ₽\n⏳ *Срок:* {tariff['duration_days']} дней\n\nОтсканируйте QR-код банковским приложением (СБП) или перейдите по [ссылке на оплату]({qr_url}).\n\n_После оплаты нажмите «✅ Я оплатил»._"
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(qr_image_data, filename='qr.png')
        await callback.message.delete()
        await callback.message.answer_photo(photo=photo, caption=text, reply_markup=yookassa_qr_kb(order_id, back_callback='pay_qr', qr_url=qr_url), parse_mode='Markdown')
    except (ValueError, RuntimeError) as e:
        logger.error(f'Ошибка создания QR ЮКасса: {e}')
        await safe_edit_or_send(callback.message, f'❌ *Ошибка создания QR*\n\n_{e}_\n\nПопробуйте другой способ оплаты.', reply_markup=home_only_kb(), parse_mode='Markdown')
    await callback.answer()

@router.callback_query(F.data.startswith('check_yookassa_qr:'))
async def check_yookassa_payment(callback: CallbackQuery, state: FSMContext):
    """
    Проверяет статус QR-платежа ЮКасса по нажатию «✅ Я оплатил».
    При успехе — запускает процесс создания ключа.
    """
    from database.requests import find_order_by_order_id, is_order_already_paid, update_payment_type, get_user_balance, deduct_from_balance
    from bot.services.billing import check_yookassa_payment_status, process_payment_order, process_referral_reward
    from bot.keyboards.admin import home_only_kb
    from bot.services.user_locks import user_locks
    order_id = callback.data.split(':', 1)[1]
    if is_order_already_paid(order_id):
        order = find_order_by_order_id(order_id)
        if order:
            await finalize_payment_ui(callback.message, state, '✅ Оплата уже была обработана ранее.', order, user_id=callback.from_user.id)
        await callback.answer()
        return
    order = find_order_by_order_id(order_id)
    if not order:
        await callback.answer('❌ Ордер не найден', show_alert=True)
        return
    yookassa_payment_id = order.get('yookassa_payment_id')
    if not yookassa_payment_id:
        await callback.answer('⚠️ Нет данных о платеже. Попробуйте чуть позже.', show_alert=True)
        return
    await callback.answer('🔍 Проверяем платёж...')
    try:
        status = await check_yookassa_payment_status(yookassa_payment_id)
    except Exception as e:
        logger.error(f'Ошибка проверки статуса ЮКасса {yookassa_payment_id}: {e}')
        await callback.message.answer('❌ Не удалось проверить статус платежа. Попробуйте позже.', reply_markup=home_only_kb(), parse_mode='Markdown')
        return
    if status == 'succeeded':
        update_payment_type(order_id, 'yookassa_qr')
        state_data = await state.get_data()
        balance_to_deduct = state_data.get('balance_to_deduct', 0)
        remaining_cents = state_data.get('remaining_cents', 0)
        try:
            (success, text, updated_order) = await process_payment_order(order_id)
            if success and updated_order:
                user_internal_id = updated_order['user_id']
                days = updated_order.get('period_days') or updated_order.get('duration_days') or 30
                if balance_to_deduct > 0:
                    async with user_locks[user_internal_id]:
                        current_balance = get_user_balance(user_internal_id)
                        actual_deduct = min(balance_to_deduct, current_balance)
                        if actual_deduct > 0:
                            deduct_from_balance(user_internal_id, actual_deduct)
                            logger.info(f'Списано {actual_deduct} коп с баланса user {user_internal_id} при частичной QR-оплате')
                await state.update_data(balance_to_deduct=0, remaining_cents=0)
                # Определяем сумму для реферального вознаграждения:
                # Если это частичная оплата (был баланс) — используем remaining_cents (сумму QR-платежа)
                # Если это обычная оплата — берём полную цену тарифа из ордера
                if remaining_cents > 0:
                    referral_amount = remaining_cents
                else:
                    # Обычная QR-оплата без частичной — берём цену тарифа в копейках рублей
                    from database.requests import get_tariff_by_id
                    _tariff = get_tariff_by_id(updated_order.get('tariff_id'))
                    referral_amount = int((_tariff.get('price_rub', 0) or 0) * 100) if _tariff else 0
                await process_referral_reward(user_internal_id, days, referral_amount, 'yookassa_qr')
                try:
                    await callback.message.delete()
                except Exception:
                    pass
                await finalize_payment_ui(callback.message, state, text, updated_order, user_id=callback.from_user.id)
            else:
                await callback.message.answer(text, reply_markup=home_only_kb(), parse_mode='Markdown')
        except Exception as e:
            from bot.errors import TariffNotFoundError
            if isinstance(e, TariffNotFoundError):
                from database.requests import get_setting
                from bot.keyboards.user import support_kb
                support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
                await callback.message.answer(str(e), reply_markup=support_kb(support_link), parse_mode='Markdown')
            else:
                logger.exception(f'Ошибка обработки QR-платежа: {e}')
                await callback.message.answer('❌ Произошла ошибка при обработке платежа.', parse_mode='Markdown')
    elif status == 'canceled':
        await callback.message.answer('❌ *Платёж отменён*\n\nПохоже, платёж был отменён или истёк срок QR-кода.\nПопробуйте снова выбрать тариф.', reply_markup=home_only_kb(), parse_mode='Markdown')
    else:
        await callback.message.answer('⏳ *Платёж ещё не поступил*\n\nОплатите QR-код и нажмите «✅ Я оплатил» снова.\n\n_Если только что оплатили — подождите пару секунд._', parse_mode='Markdown')

@router.callback_query(F.data.startswith('renew_qr_tariff:'))
async def renew_qr_select_tariff(callback: CallbackQuery):
    """Выбор тарифа для QR-оплаты при продлении ключа."""
    from database.requests import get_key_details_for_user
    from bot.keyboards.user import renew_tariff_select_kb
    from bot.utils.text import escape_md, safe_edit_or_send
    from bot.utils.groups import get_tariffs_for_renewal
    key_id = int(callback.data.split(':')[1])
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    rub_tariffs = [t for t in tariffs if t.get('price_rub') and t['price_rub'] > 0]
    if not rub_tariffs:
        await callback.answer('😔 Нет тарифов с ценой в рублях', show_alert=True)
        return
    await safe_edit_or_send(callback.message, f"📱 *QR-оплата (Карта/СБП)*\n\n🔑 Ключ: *{escape_md(key['display_name'])}*\n\nВыберите тариф для продления:", reply_markup=renew_tariff_select_kb(rub_tariffs, key_id, is_qr=True), parse_mode='Markdown')
    await callback.answer()

@router.callback_query(F.data.startswith('renew_pay_qr:'))
async def renew_qr_create(callback: CallbackQuery):
    """Создаёт QR-платёж ЮКасса для продления ключа."""
    from database.requests import get_tariff_by_id, get_user_internal_id, create_pending_order, save_yookassa_payment_id, get_key_details_for_user
    from bot.services.billing import create_yookassa_qr_payment
    from bot.keyboards.user import yookassa_qr_kb
    from bot.keyboards.admin import home_only_kb
    from bot.utils.text import escape_md, safe_edit_or_send
    parts = callback.data.split(':')
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await callback.answer('❌ Ошибка тарифа или ключа', show_alert=True)
        return
    price_rub = float(tariff.get('price_rub') or 0)
    if price_rub <= 0:
        await callback.answer('❌ Цена в рублях не задана', show_alert=True)
        return
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Пользователь не найден', show_alert=True)
        return
    (_, order_id) = create_pending_order(user_id=user_id, tariff_id=tariff_id, payment_type='yookassa_qr', vpn_key_id=key_id)
    await safe_edit_or_send(callback.message, '⏳ Создаём QR-код для оплаты...')
    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.username
        description = f"Продление Ключа «{key['display_name']}»: «{tariff['name']}» ({tariff['duration_days']} дн.)"
        result = await create_yookassa_qr_payment(amount_rub=price_rub, order_id=order_id, description=description, bot_name=bot_name)
        save_yookassa_payment_id(order_id, result['yookassa_payment_id'])
        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')
        if not qr_image_data or not qr_url:
            await safe_edit_or_send(callback.message, '❌ ЮКасса не вернула данные для оплаты. Попробуйте позже.', reply_markup=home_only_kb(), parse_mode='Markdown')
            return
        text = f"📱 *QR-код для оплаты*\n\n🔑 *Ключ:* {escape_md(key['display_name'])}\n💳 *Тариф:* {tariff['name']}\n💰 *Сумма:* {int(price_rub)} ₽\n⏳ *Продление:* +{tariff['duration_days']} дней\n\nОтсканируйте QR-код банковским приложением (СБП) или перейдите по [ссылке на оплату]({qr_url}).\n\n_После оплаты нажмите «✅ Я оплатил»._"
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(qr_image_data, filename='qr.png')
        await callback.message.delete()
        await callback.message.answer_photo(photo=photo, caption=text, reply_markup=yookassa_qr_kb(order_id, back_callback=f'renew_qr_tariff:{key_id}', qr_url=qr_url), parse_mode='Markdown')
    except (ValueError, RuntimeError) as e:
        logger.error(f'Ошибка QR ЮКасса (продление): {e}')
        await safe_edit_or_send(callback.message, f'❌ *Ошибка создания QR*\n\n_{e}_', reply_markup=home_only_kb(), parse_mode='Markdown')
    await callback.answer()