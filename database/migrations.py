"""
Система миграций базы данных.

Миграции применяются автоматически при запуске бота.
Каждая миграция имеет уникальный номер версии.

INITIAL_VERSION — версия, на которой произведено сжатие миграций.
Все миграции до этой версии включены в migration_initial().
Новые инкрементальные миграции добавляются в словарь MIGRATIONS.
"""
import sqlite3
import logging
import json
from .connection import get_db

logger = logging.getLogger(__name__)


def _add_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    """
    Добавляет колонку в таблицу, игнорируя ошибку если колонка уже существует.
    Используется в миграциях для идемпотентного добавления колонок.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            logger.info(f"Колонка {column_def.split()[0]} уже существует в {table} — пропускаем")
        else:
            raise


# Версия, на которой произведено сжатие (migration_initial создаёт БД этой версии)
INITIAL_VERSION = 21

# Текущая версия схемы БД (инкрементируется при добавлении новых миграций)
LATEST_VERSION = 21


def get_current_version() -> int:
    """
    Получает текущую версию схемы БД.
    
    Returns:
        int: Номер версии (0 если таблица версий не существует)
    """
    with get_db() as conn:
        # Проверяем существование таблицы schema_version
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        if not cursor.fetchone():
            return 0
        
        cursor = conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        return row["version"] if row else 0


def set_version(conn: sqlite3.Connection, version: int) -> None:
    """
    Устанавливает версию схемы БД.
    
    Args:
        conn: Соединение с БД
        version: Номер версии
    """
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


# ═══════════════════════════════════════════════════════════════════════════════
# Начальная миграция (сжатие v1–v21)
# ═══════════════════════════════════════════════════════════════════════════════

def migration_initial(conn: sqlite3.Connection) -> None:
    """
    Начальная миграция: создаёт полную актуальную схему БД (v21).
    
    Вызывается только при новой установке (version = 0).
    Сжимает миграции v1–v21 в одну функцию.
    
    Таблицы:
    - schema_version: версия схемы
    - settings: глобальные настройки бота
    - users: пользователи Telegram
    - tariffs: тарифные планы
    - tariff_groups: группы тарифов
    - servers: VPN-серверы (3X-UI)
    - server_groups: связь серверов с группами (many-to-many)
    - vpn_keys: ключи/подписки пользователей
    - payments: история оплат
    - notification_log: лог уведомлений
    - referral_levels: уровни реферальной системы
    - referral_stats: статистика по рефералам
    - pages: страницы пользовательского интерфейса
    """
    logger.info("Создание БД (актуальная схема v21)...")

    # ── schema_version ────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )
    """)

    # ── settings ──────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    default_settings = [
        ('broadcast_filter', 'all'),
        ('broadcast_in_progress', '0'),
        ('notification_days', '3'),
        ('notification_text',
         '⚠️ <b>Ваш VPN-ключ %имяключа% скоро истекает!</b>\n\n'
         'Через %дней% дней закончится срок действия вашего ключа.\n\n'
         'Продлите подписку, чтобы сохранить доступ к VPN без перерыва!'),
        ('trial_enabled', '0'),
        ('trial_tariff_id', ''),
        ('cards_enabled', '0'),
        ('cards_provider_token', ''),
        ('yookassa_qr_enabled', '0'),
        ('yookassa_shop_id', ''),
        ('yookassa_secret_key', ''),
        ('crypto_enabled', '0'),
        ('crypto_item_url', ''),
        ('crypto_secret_key', ''),
        ('crypto_integration_mode', 'simple'),
        ('stars_enabled', '0'),
        ('demo_payment_enabled', '0'),
        ('traffic_notification_text',
         '⚠️ По ключу <b>{keyname}</b> осталось {percent}% трафика ({used} из {limit})'),
        ('monthly_traffic_reset_enabled', '0'),
        ('referral_enabled', '0'),
        ('referral_reward_type', 'days'),
        ('usd_rub_rate', '9500'),
        ('update_blocked', '0'),
    ]
    for key, value in default_settings:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    # ── users ─────────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL UNIQUE,
            username TEXT,
            is_banned INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_trial INTEGER DEFAULT 0,
            referral_code TEXT,
            referred_by INTEGER REFERENCES users(id),
            personal_balance INTEGER DEFAULT 0,
            referral_coefficient REAL DEFAULT 1.0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")

    # ── tariffs ───────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            duration_days INTEGER NOT NULL,
            price_cents INTEGER NOT NULL,
            price_stars INTEGER NOT NULL,
            external_id INTEGER,
            display_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            price_rub INTEGER DEFAULT 0,
            traffic_limit_gb INTEGER DEFAULT 0,
            group_id INTEGER DEFAULT 1
        )
    """)

    # Скрытый тариф для админских ключей
    conn.execute("""
        INSERT INTO tariffs (name, duration_days, price_cents, price_stars, external_id, display_order, is_active)
        SELECT 'Admin Tariff', 365, 0, 0, 0, 999, 0
        WHERE NOT EXISTS (SELECT 1 FROM tariffs WHERE name = 'Admin Tariff')
    """)

    # ── tariff_groups ─────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tariff_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        INSERT OR IGNORE INTO tariff_groups (id, name, sort_order)
        VALUES (1, 'Основная', 1)
    """)

    # ── servers ───────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL,
            web_base_path TEXT NOT NULL,
            login TEXT NOT NULL,
            password TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            protocol TEXT DEFAULT 'https'
        )
    """)

    # ── server_groups ─────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS server_groups (
            server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
            group_id  INTEGER NOT NULL REFERENCES tariff_groups(id) ON DELETE CASCADE,
            PRIMARY KEY (server_id, group_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_server_groups_group ON server_groups(group_id)")

    # ── vpn_keys ──────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vpn_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            server_id INTEGER,
            tariff_id INTEGER NOT NULL,
            panel_inbound_id INTEGER,
            client_uuid TEXT,
            panel_email TEXT,
            custom_name TEXT,
            expires_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            traffic_used INTEGER DEFAULT 0,
            traffic_limit INTEGER DEFAULT 0,
            traffic_updated_at DATETIME,
            traffic_notified_pct INTEGER DEFAULT 100,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (server_id) REFERENCES servers(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_user_id ON vpn_keys(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpn_keys_expires_at ON vpn_keys(expires_at)")

    # ── payments ──────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER,
            user_id INTEGER NOT NULL,
            tariff_id INTEGER,
            order_id TEXT NOT NULL UNIQUE,
            payment_type TEXT,
            amount_cents INTEGER,
            amount_stars INTEGER,
            period_days INTEGER,
            status TEXT DEFAULT 'paid',
            paid_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            yookassa_payment_id TEXT,
            FOREIGN KEY (vpn_key_id) REFERENCES vpn_keys(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments(paid_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id)")

    # ── notification_log ──────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER NOT NULL,
            sent_at DATE NOT NULL,
            FOREIGN KEY (vpn_key_id) REFERENCES vpn_keys(id)
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_log_unique ON notification_log(vpn_key_id, sent_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_log_vpn_key ON notification_log(vpn_key_id)")

    # ── referral_levels ───────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS referral_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level_number INTEGER NOT NULL UNIQUE,
            percent INTEGER NOT NULL,
            enabled INTEGER DEFAULT 1
        )
    """)
    conn.execute("INSERT OR IGNORE INTO referral_levels (level_number, percent, enabled) VALUES (1, 10, 1)")
    conn.execute("INSERT OR IGNORE INTO referral_levels (level_number, percent, enabled) VALUES (2, 5, 0)")
    conn.execute("INSERT OR IGNORE INTO referral_levels (level_number, percent, enabled) VALUES (3, 2, 0)")

    # ── referral_stats ────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS referral_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referral_id INTEGER NOT NULL,
            level INTEGER NOT NULL,
            total_payments_count INTEGER DEFAULT 0,
            total_reward_cents INTEGER DEFAULT 0,
            total_reward_days INTEGER DEFAULT 0,
            FOREIGN KEY (referrer_id) REFERENCES users(id),
            FOREIGN KEY (referral_id) REFERENCES users(id),
            UNIQUE (referrer_id, referral_id, level)
        )
    """)

    # ── pages ─────────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            page_key         TEXT PRIMARY KEY,
            text_default     TEXT NOT NULL DEFAULT '',
            image_default    TEXT,
            buttons_default  TEXT NOT NULL DEFAULT '[]',
            text_custom      TEXT,
            image_custom     TEXT,
            updated_at       TIMESTAMP,
            buttons_custom   TEXT
        )
    """)

    # Дефолтные данные страниц (тексты в HTML, кнопки в JSON)
    page_defaults = {
        'main': {
            'text': (
                "🔐 <b>Добро пожаловать в VPN-бот!</b>\n\n"
                "Быстрый, безопасный и анонимный доступ к интернету.\n"
                "Без логов, без ограничений, без проблем! 🚀\n\n"
                "%тарифы%"
            ),
            'buttons': json.dumps([
                {"id": "btn_my_keys",  "label": "🔑 Мои ключи",         "color": "primary",   "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_my_keys"},
                {"id": "btn_buy_key",  "label": "💳 Купить ключ",        "color": "primary",   "row": 0, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_buy"},
                {"id": "btn_trial",    "label": "🎁 Пробная подписка",   "color": "secondary", "row": 1, "col": 0, "is_hidden": True,  "action_type": "internal", "action_value": "cmd_trial"},
                {"id": "btn_referral", "label": "🔗 Реферальная ссылка",  "color": "secondary", "row": 2, "col": 0, "is_hidden": True,  "action_type": "internal", "action_value": "cmd_referral"},
                {"id": "btn_help",     "label": "❓ Справка",             "color": "secondary", "row": 2, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_help"},
            ], ensure_ascii=False),
        },
        'help': {
            'text': (
                "🔐 Этот бот предоставляет доступ к VPN-сервису.\n\n"
                "<b>Как это работает:</b>\n"
                "1. Купите ключ через раздел «Купить ключ»\n\n"
                "2. Установите VPN-клиент для вашего устройства:\n\n"
                "Hiddify или v2rayNG или V2Box\n"
                "Подробная инструкция по настройке VPN👇 https://telegra.ph/Kak-nastroit-VPN-Gajd-za-2-minuty-01-23\n\n"
                "3. Импортируйте ключ в приложение\n\n"
                "4. Подключайтесь и наслаждайтесь! 🚀\n\n"
                "---\n"
                "Разработчик @plushkin_blog\n"
                "---"
            ),
            'buttons': json.dumps([
                {"id": "btn_news",      "label": "📢 Новости",    "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_support",   "label": "💬 Поддержка",  "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
        'trial': {
            'text': (
                "🎁 <b>Пробная подписка</b>\n\n"
                "Хотите попробовать наш VPN бесплатно?\n\n"
                "Мы предлагаем пробный период, чтобы вы могли убедиться в качестве "
                "и скорости нашего сервиса.\n\n"
                "<b>Что входит в пробный доступ:</b>\n"
                "• Полный доступ к VPN без ограничений по сайтам\n"
                "• Высокая скорость соединения\n"
                "• Несколько протоколов на выбор\n\n"
                "Нажмите кнопку ниже, чтобы активировать пробный доступ прямо сейчас!\n\n"
                "<i>Пробный период предоставляется один раз на аккаунт.</i>"
            ),
            'buttons': json.dumps([
                {"id": "btn_activate_trial", "label": "✅ Активировать",  "color": "primary",   "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_activate_trial"},
                {"id": "btn_back_main",      "label": "🈴 На главную",   "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
        'prepayment': {
            'text': (
                "💳 <b>Купить ключ</b>\n\n"
                "🔐 <b>Что вы получаете:</b>\n"
                "• Доступ к нескольким серверам и протоколам\n"
                "• 1 ключ = 1 устройство (одновременное подключение)\n"
                "• Лимит трафика: до 1 ТБ в месяц (сброс каждые 30 дней)\n\n"
                "⚠️ <b>Важно знать:</b>\n"
                "• Средства не возвращаются — услуга считается оказанной в момент получения ключа\n"
                "• Мы не даём никаких гарантий бесперебойной работы сервиса в будущем\n"
                "• Мы не можем гарантировать, что данная технология останется рабочей\n\n"
                "<i>Приобретая ключ, вы соглашаетесь с этими условиями.</i>"
            ),
            'buttons': json.dumps([
                {"id": "btn_pay_crypto",  "label": "🪙 Оплатить USDT",          "color": "primary",   "row": 0, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_stars",   "label": "⭐ Оплатить звёздами",      "color": "primary",   "row": 1, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_cards",   "label": "💳 Оплатить картой",        "color": "primary",   "row": 2, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_qr",      "label": "📱 QR-оплата (Карта/СБП)",  "color": "primary",   "row": 3, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_demo",    "label": "🏦 Демо оплата (РФ карта)", "color": "primary",   "row": 4, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_pay_balance", "label": "💎 Использовать баланс",    "color": "primary",   "row": 5, "col": 0, "is_hidden": False, "action_type": "system", "action_value": None},
                {"id": "btn_back_main",   "label": "🈴 На главную",             "color": "secondary", "row": 6, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
        'referral': {
            'text': (
                "👥 <b>Реферальная система</b>\n\n"
                "📎 Ваша реферальная ссылка:\n"
                "<code>%ссылка%</code>\n\n"
                "━━━━━━━━━━━━━━━\n"
                "📝 <b>Условия:</b>\n"
                "Приглашённые пользователи регистрируются по вашей ссылке. "
                "Когда они оплачивают подписку, вы получаете реферальное вознаграждение.\n\n"
                "━━━━━━━━━━━━━━━\n"
                "%статистика%"
            ),
            'buttons': json.dumps([
                {"id": "btn_back_main", "label": "🈴 На главную", "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
        'key_delivery': {
            'text': (
                "✅ <b>Ваш VPN-ключ!</b>\n\n"
                "%ключ%\n"
                "☝️ Нажмите, чтобы скопировать.\n\n"
                "📱 <b>Инструкция:</b>\n"
                "1. Скопируйте ссылку или отсканируйте QR-код.\n"
                "2. Импортируйте в свой клиент. Какой именно клиент подходит, смотри в инструкции по кнопке ниже.\n"
                "3. Нажмите подключиться!"
            ),
            'buttons': json.dumps([
                {"id": "btn_help",      "label": "📄 Инструкция",  "color": "secondary", "row": 0, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_help"},
                {"id": "btn_my_keys",   "label": "🔑 Мои ключи",  "color": "secondary", "row": 0, "col": 1, "is_hidden": False, "action_type": "internal", "action_value": "cmd_my_keys"},
                {"id": "btn_back_main", "label": "🈴 На главную",  "color": "secondary", "row": 1, "col": 0, "is_hidden": False, "action_type": "internal", "action_value": "cmd_back_main"},
            ], ensure_ascii=False),
        },
    }

    for page_key, data in page_defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO pages (page_key, text_default, buttons_default) VALUES (?, ?, ?)",
            (page_key, data['text'], data['buttons'])
        )

    logger.info("БД создана (актуальная схема v21)")


# ═══════════════════════════════════════════════════════════════════════════════
# Инкрементальные миграции (добавляются ниже по мере развития проекта)
# ═══════════════════════════════════════════════════════════════════════════════

# Пример добавления новой миграции:
#
# def migration_22(conn: sqlite3.Connection) -> None:
#     """Миграция v22: Описание изменений."""
#     logger.info("Применение миграции v22...")
#     # ... изменения ...
#     logger.info("Миграция v22 применена")

MIGRATIONS = {
    # 22: migration_22,
}


def run_migrations() -> None:
    """
    Запускает все необходимые миграции.
    
    Логика:
    - version = 0 (новая установка): вызывает migration_initial → ставит LATEST_VERSION
    - version = LATEST_VERSION: ничего не делает
    - version < INITIAL_VERSION: ошибка (нужно обновить через промежуточную версию)
    - version >= INITIAL_VERSION: применяет инкрементальные миграции из MIGRATIONS
    """
    try:
        current = get_current_version()
        
        if current >= LATEST_VERSION:
            logger.info(f"✅ БД соответствует версии {LATEST_VERSION}. Миграция не требуется.")
            return
        
        # Защита: БД на промежуточной версии, которую нельзя обновить сжатыми миграциями
        if 0 < current < INITIAL_VERSION:
            raise RuntimeError(
                f"Версия БД ({current}) ниже минимально поддерживаемой ({INITIAL_VERSION}). "
                f"Сначала обновите бот до промежуточной версии, чтобы БД мигрировала до v{INITIAL_VERSION}."
            )
        
        logger.info(f"🔄 Требуется миграция БД с версии {current} до {LATEST_VERSION}")
        
        with get_db() as conn:
            # Новая установка — создаём БД с нуля
            if current == 0:
                migration_initial(conn)
                set_version(conn, INITIAL_VERSION)
                current = INITIAL_VERSION
            
            # Инкрементальные миграции (22, 23, ...)
            for version in range(current + 1, LATEST_VERSION + 1):
                if version in MIGRATIONS:
                    logger.info(f"🚀 Применяю миграцию v{version}...")
                    MIGRATIONS[version](conn)
                    set_version(conn, version)
        
        logger.info(f"✅ Миграция успешная: БД обновлена до версии {LATEST_VERSION}")
        
    except Exception as e:
        logger.error(f"❌ Неуспешная миграция: {e}")
        raise
