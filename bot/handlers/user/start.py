import logging
import uuid
import asyncio
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError
from config import ADMIN_IDS
from database.requests import get_or_create_user, is_user_banned, get_all_servers, get_setting, is_referral_enabled, get_user_by_referral_code, set_user_referrer
from bot.keyboards.user import main_menu_kb
from bot.states.user_states import RenameKey, ReplaceKey
from bot.utils.text import escape_md

logger = logging.getLogger(__name__)
router = Router()

def get_welcome_text(is_admin: bool=False) -> str:
    """Формирует приветственный текст с реальными тарифами из БД."""
    from database.requests import get_all_tariffs, get_setting, is_crypto_configured, is_stars_enabled, is_cards_enabled, is_yookassa_qr_configured
    from bot.utils.text import escape_md2
    welcome_text = get_setting('main_page_text', '🔐 *Добро пожаловать в VPN\\-бот\\!*')
    crypto_enabled = is_crypto_configured()
    stars_enabled = is_stars_enabled()
    cards_enabled = is_cards_enabled()
    yookassa_qr_enabled = is_yookassa_qr_configured()
    tariffs = get_all_tariffs()
    tariff_lines = []
    if tariffs:
        tariff_lines.append('📋 *Тарифы:*')
        for tariff in tariffs:
            prices = []
            if crypto_enabled:
                price_usd = tariff['price_cents'] / 100
                price_str = f'{price_usd:g}'.replace('.', ',')
                prices.append(f'${escape_md2(price_str)}')
            if stars_enabled:
                prices.append(f"{tariff['price_stars']} ⭐")
            if (cards_enabled or yookassa_qr_enabled) and tariff.get('price_rub', 0) > 0:
                prices.append(f"{int(tariff['price_rub'])} ₽")
            price_display = ' \\/ '.join(prices) if prices else 'Цена не установлена'
            tariff_lines.append(f"• {escape_md2(tariff['name'])} — {price_display}")
    tariff_text = '\n'.join(tariff_lines)
    if '%без\\_тарифов%' in welcome_text:
        return welcome_text.replace('%без\\_тарифов%', '')
    if '%тарифы%' not in welcome_text:
        welcome_text = f'{welcome_text}\n\n%тарифы%'
    return welcome_text.replace('%тарифы%', tariff_text)

@router.message(Command('start'), StateFilter('*'))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    """Обработчик команды /start."""
    user_id = message.from_user.id
    username = message.from_user.username
    logger.info(f'CMD_START: User {user_id} started bot')
    await state.clear()
    (user, is_new) = get_or_create_user(user_id, username)
    if user.get('is_banned'):
        await message.answer('⛔ *Доступ заблокирован*\n\nВаш аккаунт заблокирован. Обратитесь в поддержку.', parse_mode='Markdown')
        return
    is_admin = user_id in ADMIN_IDS
    text = get_welcome_text(is_admin)
    args = command.args
    if args and args.startswith('bill'):
        from bot.services.billing import process_crypto_payment
        from bot.handlers.user.payments.base import finalize_payment_ui
        try:
            (success, text, order) = await process_crypto_payment(args, user_id=user['id'])
            if success and order:
                await finalize_payment_ui(message, state, text, order, user_id=message.from_user.id)
            else:
                await message.answer(text, parse_mode='Markdown')
        except Exception as e:
            from bot.errors import TariffNotFoundError
            if isinstance(e, TariffNotFoundError):
                from bot.database.requests import get_setting
                from bot.keyboards.user import support_kb
                support_link = get_setting('support_channel_link', 'https://t.me/YadrenoChat')
                await message.answer(str(e), reply_markup=support_kb(support_link), parse_mode='Markdown')
            else:
                logger.exception(f'Ошибка обработки платежа: {e}')
                await message.answer('❌ Произошла ошибка при обработке платежа.', parse_mode='Markdown')
        return
    if is_new and args and args.startswith('ref_'):
        ref_code = args[4:]
        referrer = get_user_by_referral_code(ref_code)
        if referrer and referrer['id'] != user['id']:
            if set_user_referrer(user['id'], referrer['id']):
                logger.info(f"User {user_id} привязан к рефереру {referrer['telegram_id']}")
    from database.requests import is_trial_enabled, get_trial_tariff_id, has_used_trial
    show_trial = is_trial_enabled() and get_trial_tariff_id() is not None and (not has_used_trial(user_id))
    show_referral = is_referral_enabled()
    try:
        await message.answer(text, reply_markup=main_menu_kb(is_admin=is_admin, show_trial=show_trial, show_referral=show_referral), parse_mode='MarkdownV2')
    except TelegramForbiddenError:
        logger.warning(f'User {user_id} blocked the bot during /start')
    except Exception as e:
        logger.error(f'Error sending start message to {user_id}: {e}')

@router.callback_query(F.data == 'start')
async def callback_start(callback: CallbackQuery, state: FSMContext):
    """Возврат на главный экран по кнопке."""
    user_id = callback.from_user.id
    if is_user_banned(user_id):
        await callback.answer('⛔ Доступ заблокирован', show_alert=True)
        return
    await state.clear()
    is_admin = user_id in ADMIN_IDS
    text = get_welcome_text(is_admin)
    from database.requests import is_trial_enabled, get_trial_tariff_id, has_used_trial
    show_trial = is_trial_enabled() and get_trial_tariff_id() is not None and (not has_used_trial(user_id))
    show_referral = is_referral_enabled()
    try:
        await callback.message.edit_text(text, reply_markup=main_menu_kb(is_admin=is_admin, show_trial=show_trial, show_referral=show_referral), parse_mode='MarkdownV2')
    except Exception:
        try:
            await callback.message.delete()
        except:
            pass
        await callback.message.answer(text, reply_markup=main_menu_kb(is_admin=is_admin, show_trial=show_trial, show_referral=show_referral), parse_mode='MarkdownV2')
    await callback.answer()

@router.message(Command('help'))
async def cmd_help(message: Message, state: FSMContext):
    """Обработчик команды /help - вызывает логику кнопки 'Справка'."""
    if is_user_banned(message.from_user.id):
        await message.answer('⛔ *Доступ заблокирован*\n\nВаш аккаунт заблокирован. Обратитесь в поддержку.', parse_mode='Markdown')
        return
    await state.clear()
    await show_help(message.answer)

async def show_help(send_function):
    """
    Общая логика для показа справки.
    
    Args:
        send_function: Функция для отправки сообщения (message.answer или callback.message.edit_text)
    """
    from bot.keyboards.admin import home_only_kb
    from bot.keyboards.user import help_kb
    from database.requests import get_setting
    help_text = get_setting('help_page_text', '❓ *Справка*')
    default_news = 'https://t.me/YadrenoRu'
    default_support = 'https://t.me/YadrenoChat'
    news_link = get_setting('news_channel_link', default_news)
    support_link = get_setting('support_channel_link', default_support)
    if not news_link or not news_link.startswith(('http://', 'https://')):
        news_link = default_news
    if not support_link or not support_link.startswith(('http://', 'https://')):
        support_link = default_support
    news_hidden = get_setting('news_hidden', '0') == '1'
    support_hidden = get_setting('support_hidden', '0') == '1'
    news_name = get_setting('news_button_name', 'Новости')
    support_name = get_setting('support_button_name', 'Поддержка')
    await send_function(help_text, reply_markup=help_kb(news_link, support_link, news_hidden=news_hidden, support_hidden=support_hidden, news_name=news_name, support_name=support_name), parse_mode='MarkdownV2')

@router.callback_query(F.data == 'help')
async def help_handler(callback: CallbackQuery):
    """Показывает справку по кнопке."""
    try:
        await show_help(callback.message.edit_text)
    except Exception:
        try:
            await callback.message.delete()
        except:
            pass
        await show_help(callback.message.answer)
    await callback.answer()

@router.callback_query(F.data == 'help')
async def help_stub(callback: CallbackQuery):
    """Раздел справки."""
    try:
        await show_help(callback.message.edit_text)
    except Exception:
        try:
            await callback.message.delete()
        except:
            pass
        await show_help(callback.message.answer)
    await callback.answer()

@router.callback_query(F.data == 'noop')
async def noop_handler(callback: CallbackQuery):
    """Заглушка: нажатие на заголовок группы ничего не делает."""
    await callback.answer()