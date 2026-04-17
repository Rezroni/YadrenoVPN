"""
Реестр действий кнопок страниц.

Содержит:
- ACTION_REGISTRY: маппинг action_value → callback_data для internal-кнопок
- SYSTEM_BUTTONS: маппинг button_id → handler(context) для system-кнопок

Правила:
- action_value — контракт, НЕЛЬЗЯ менять после релиза
- button_id — контракт, НЕЛЬЗЯ менять после релиза
"""
import logging
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)


# =============================================================================
# ACTION_REGISTRY: internal-кнопки
# Ключ = action_value из buttons_default, Значение = callback_data для Telegram
# =============================================================================

ACTION_REGISTRY: Dict[str, str] = {
    "cmd_buy":            "buy_key",
    "cmd_my_keys":        "my_keys",
    "cmd_help":           "help",
    "cmd_back_main":      "start",
    "cmd_trial":          "trial_subscription",
    "cmd_referral":       "referral_system",
    "cmd_activate_trial": "trial_activate",
}


# =============================================================================
# SYSTEM_BUTTONS: system-кнопки
#
# Каждый handler получает context: dict и возвращает:
# - dict с ключами: callback_data, url, label, hidden (все опциональные)
# - None — кнопка полностью скрывается
#
# context содержит данные, переданные хендлером в render_page:
# - order_id, telegram_id, и другие параметры
# =============================================================================


def _resolve_pay_crypto(ctx: dict) -> Optional[dict]:
    """Кнопка оплаты криптой (USDT). Определяет видимость и формирует action."""
    from database.requests import is_crypto_configured, get_crypto_integration_mode, get_setting
    from bot.services.billing import build_crypto_payment_url, extract_item_id_from_url

    if not is_crypto_configured():
        return None

    mode = get_crypto_integration_mode()
    order_id = ctx.get('order_id')

    if mode == 'simple':
        cb = f"pay_crypto:{order_id}" if order_id else "pay_crypto"
        return {"callback_data": cb}
    else:
        # standard — формируем URL
        crypto_item_url = get_setting('crypto_item_url')
        item_id = extract_item_id_from_url(crypto_item_url)
        if item_id and order_id:
            url = build_crypto_payment_url(
                item_id=item_id,
                invoice_id=order_id,
                tariff_external_id=None,
                price_cents=None
            )
            return {"url": url}
        return None


def _resolve_pay_stars(ctx: dict) -> Optional[dict]:
    """Кнопка оплаты звёздами."""
    from database.requests import is_stars_enabled

    if not is_stars_enabled():
        return None

    order_id = ctx.get('order_id')
    cb = f"pay_stars:{order_id}" if order_id else "pay_stars"
    return {"callback_data": cb}


def _resolve_pay_cards(ctx: dict) -> Optional[dict]:
    """Кнопка оплаты картой (Telegram Payments)."""
    from database.requests import is_cards_enabled

    if not is_cards_enabled():
        return None

    order_id = ctx.get('order_id')
    cb = f"pay_cards:{order_id}" if order_id else "pay_cards"
    return {"callback_data": cb}


def _resolve_pay_qr(ctx: dict) -> Optional[dict]:
    """Кнопка QR-оплаты (ЮКасса)."""
    from database.requests import is_yookassa_qr_configured

    if not is_yookassa_qr_configured():
        return None

    return {"callback_data": "pay_qr"}


def _resolve_pay_demo(ctx: dict) -> Optional[dict]:
    """Кнопка демо-оплаты (РФ карта)."""
    from database.requests import is_demo_payment_enabled

    if not is_demo_payment_enabled():
        return None

    order_id = ctx.get('order_id')
    cb = f"demo_tariffs:{order_id}" if order_id else "demo_tariffs"
    return {"callback_data": cb}


def _resolve_pay_balance(ctx: dict) -> Optional[dict]:
    """Кнопка «Использовать баланс». Видна только при referral + balance > 0."""
    from database.requests import (
        is_referral_enabled, get_referral_reward_type,
        get_user_balance, get_user_internal_id,
    )

    if not is_referral_enabled() or get_referral_reward_type() != 'balance':
        return None

    telegram_id = ctx.get('telegram_id')
    if not telegram_id:
        return None

    user_id = get_user_internal_id(telegram_id)
    if not user_id:
        return None

    balance_cents = get_user_balance(user_id)
    if balance_cents <= 0:
        return None

    return {"callback_data": "pay_use_balance"}



# Карта: button_id → handler
SYSTEM_BUTTONS: Dict[str, Callable[[dict], Optional[dict]]] = {
    "btn_pay_crypto":  _resolve_pay_crypto,
    "btn_pay_stars":   _resolve_pay_stars,
    "btn_pay_cards":   _resolve_pay_cards,
    "btn_pay_qr":      _resolve_pay_qr,
    "btn_pay_demo":    _resolve_pay_demo,
    "btn_pay_balance": _resolve_pay_balance,
}
