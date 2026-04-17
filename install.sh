#!/bin/bash

# Yadreno VPN — скрипт установки и управления
# Запуск: bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh)

set -e

INSTALL_DIR="/root/YadrenoVPN"
REPO_URL="https://github.com/plushkinv/YadrenoVPN.git"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_FILE="yadreno-vpn.service"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

print_header() {
    echo -e "\n${CYAN}========================================${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}========================================${NC}\n"
}

print_ok() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_err() {
    echo -e "${RED}[✗]${NC} $1"
}

# Запрос настроек у пользователя
ask_config() {
    print_header "Настройка конфигурации"

    if [ -f "$INSTALL_DIR/config.py" ]; then
        echo -e "${YELLOW}Обнаружен существующий config.py${NC}"
        read -p "Использовать существующие настройки? (Y/n): " use_existing
        use_existing=${use_existing:-Y}
        if [[ "$use_existing" =~ ^[YyДд]$ ]]; then
            print_ok "Используем существующий config.py"
            return 0
        fi
    fi

    echo ""
    echo -e "${CYAN}Введите данные для настройки бота:${NC}"
    echo ""

    while true; do
        read -p "BOT_TOKEN (от @BotFather): " bot_token
        if [ -n "$bot_token" ]; then
            break
        fi
        print_err "BOT_TOKEN не может быть пустым!"
    done

    while true; do
        read -p "ADMIN_IDS (ваш Telegram ID): " admin_id
        if [ -n "$admin_id" ] && [[ "$admin_id" =~ ^[0-9]+$ ]]; then
            break
        fi
        print_err "ADMIN_IDS должен быть числом!"
    done

    BOT_TOKEN="$bot_token"
    ADMIN_ID="$admin_id"
    NEED_WRITE_CONFIG=1
    print_ok "Данные получены"
}

# Создание/обновление config.py
write_config() {
    if [ "$NEED_WRITE_CONFIG" != "1" ]; then
        return 0
    fi

    cp "$INSTALL_DIR/config.py.example" "$INSTALL_DIR/config.py"

    sed -i "s|\"ВАШ_ТОКЕН_БОТА\"|\"$BOT_TOKEN\"|g" "$INSTALL_DIR/config.py"
    sed -i "s|12345678|$ADMIN_ID|g" "$INSTALL_DIR/config.py"

    print_ok "config.py создан с вашими настройками"
}

# Установка системных пакетов
install_system_deps() {
    print_header "Установка системных зависимостей"

    export DEBIAN_FRONTEND=noninteractive
    export NEEDRESTART_MODE=a

    apt-get update -qq
    apt-get install -y -qq \
        python3-venv \
        python3-pip \
        git \
        > /dev/null 2>&1

    print_ok "Системные пакеты обновлены"
    print_ok "python3-venv, python3-pip, git установлены"
}

# Создание виртуального окружения и установка зависимостей
setup_venv() {
    print_header "Настройка виртуального окружения Python"

    python3 -m venv "$VENV_DIR"
    print_ok "Виртуальное окружение создано: $VENV_DIR"

    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip -q
    pip install --upgrade -r "$INSTALL_DIR/requirements.txt" -q
    deactivate

    print_ok "Зависимости Python установлены в venv"
}

# Настройка systemd сервиса
setup_systemd() {
    print_header "Настройка автозапуска (systemd)"

    cat > "$INSTALL_DIR/$SERVICE_FILE" << EOF
[Unit]
Description=Yadreno VPN Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    cp "$INSTALL_DIR/$SERVICE_FILE" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable yadreno-vpn > /dev/null 2>&1

    print_ok "systemd сервис установлен и включён в автозапуск"
}

# Запуск сервиса
start_service() {
    systemctl start yadreno-vpn
    sleep 2

    if systemctl is-active --quiet yadreno-vpn; then
        print_ok "Бот запущен и работает!"
    else
        print_err "Бот не запустился. Проверьте логи:"
        echo "  systemctl status yadreno-vpn"
        echo "  journalctl -u yadreno-vpn -n 50"
    fi
}

# ============================================================
# ПУНКТ 1: УСТАНОВКА
# ============================================================
do_install() {
    print_header "🚀 Установка Yadreno VPN"

    # Проверяем, не установлен ли уже
    if [ -d "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR/.git" ]; then
        print_warn "Yadreno VPN уже установлен в $INSTALL_DIR"
        echo ""
        echo "  1) Переустановить (удалить и установить заново)"
        echo "  2) Отмена"
        read -p "Выберите [1-2]: " reinstall_choice
        if [ "$reinstall_choice" != "1" ]; then
            echo "Установка отменена."
            return 0
        fi
        systemctl stop yadreno-vpn 2>/dev/null || true
        # Сохраняем config.py и базу данных
        if [ -f "$INSTALL_DIR/config.py" ]; then
            cp "$INSTALL_DIR/config.py" /tmp/yadreno_config_backup.py
            BACKUP_CONFIG=1
        fi
        if [ -f "$INSTALL_DIR/vpn_bot.db" ]; then
            cp "$INSTALL_DIR/vpn_bot.db" /tmp/yadreno_db_backup.db
            BACKUP_DB=1
        fi
        rm -rf "$INSTALL_DIR"
    fi

    # Запрашиваем настройки до начала установки
    ask_config

    # Установка системных зависимостей
    install_system_deps

    # Клонирование репозитория
    print_header "Загрузка Yadreno VPN"
    git clone "$REPO_URL" "$INSTALL_DIR" -q
    cd "$INSTALL_DIR"
    print_ok "Репозиторий клонирован"

    # Восстановление backup'ов при переустановке
    if [ "$BACKUP_CONFIG" = "1" ] && [ -f "/tmp/yadreno_config_backup.py" ]; then
        cp /tmp/yadreno_config_backup.py "$INSTALL_DIR/config.py"
        rm /tmp/yadreno_config_backup.py
        print_ok "config.py восстановлен из резервной копии"
        NEED_WRITE_CONFIG=0
    fi
    if [ "$BACKUP_DB" = "1" ] && [ -f "/tmp/yadreno_db_backup.db" ]; then
        cp /tmp/yadreno_db_backup.db "$INSTALL_DIR/vpn_bot.db"
        rm /tmp/yadreno_db_backup.db
        print_ok "База данных восстановлена из резервной копии"
    fi

    # Запись config.py
    write_config

    # Виртуальное окружение и зависимости
    setup_venv

    # Настройка автозапуска
    setup_systemd

    # Запуск
    print_header "Запуск бота"
    start_service

    print_header "✅ Установка завершена!"
    echo -e "  Директория: ${GREEN}$INSTALL_DIR${NC}"
    echo -e "  Виртуальное окружение: ${GREEN}$VENV_DIR${NC}"
    echo -e "  Управление сервисом:"
    echo -e "    ${CYAN}systemctl status yadreno-vpn${NC}   — статус"
    echo -e "    ${CYAN}systemctl restart yadreno-vpn${NC}  — перезапуск"
    echo -e "    ${CYAN}systemctl stop yadreno-vpn${NC}     — остановка"
    echo -e "    ${CYAN}journalctl -u yadreno-vpn -f${NC}   — логи"
}

# ============================================================
# ПУНКТ 2: МЯГКОЕ ОБНОВЛЕНИЕ (git pull)
# ============================================================
do_soft_update() {
    print_header "🔄 Мягкое обновление"

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        print_err "Yadreno VPN не установлен в $INSTALL_DIR"
        return 1
    fi

    cd "$INSTALL_DIR"

    # Сохраняем текущие изменения в stash (если есть)
    if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
        print_warn "Обнаружены локальные изменения — сохраняем через git stash"
        git stash -q
        STASHED=1
    fi

    git pull -q origin main

    if [ "$STASHED" = "1" ]; then
        git stash pop -q 2>/dev/null || print_warn "Не удалось восстановить локальные изменения (конфликт)"
    fi

    print_ok "Код обновлён"

    # Обновляем зависимости
    source "$VENV_DIR/bin/activate"
    pip install --upgrade -r requirements.txt -q
    deactivate
    print_ok "Зависимости обновлены"

    # Перезапуск
    systemctl restart yadreno-vpn
    sleep 2

    if systemctl is-active --quiet yadreno-vpn; then
        print_ok "Бот перезапущен и работает!"
    else
        print_err "Бот не запустился после обновления"
        echo "  systemctl status yadreno-vpn"
    fi
}

# ============================================================
# ПУНКТ 3: ЖЁСТКАЯ ПЕРЕЗАПИСЬ (git fetch + reset)
# ============================================================
do_hard_reset() {
    print_header "⚠️  Жёсткая перезапись"

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        print_err "Yadreno VPN не установлен в $INSTALL_DIR"
        return 1
    fi

    echo -e "${RED}Внимание! Все локальные изменения в коде будут перезаписаны.${NC}"
    echo -e "${YELLOW}config.py и vpn_bot.db затронуты НЕ будут.${NC}"
    read -p "Продолжить? (y/N): " confirm
    if [[ ! "$confirm" =~ ^[YyДд]$ ]]; then
        echo "Отменено."
        return 0
    fi

    cd "$INSTALL_DIR"

    # Жёсткая перезапись: config.py и vpn_bot.db в .gitignore — не затрагиваются
    git fetch origin -q
    git reset --hard origin/main -q
    git clean -fd -q
    print_ok "Код перезаписан из GitHub (config.py и база не затронуты)"

    # Обновляем зависимости
    source "$VENV_DIR/bin/activate"
    pip install --upgrade -r requirements.txt -q
    deactivate
    print_ok "Зависимости обновлены"

    # Перезапуск
    systemctl restart yadreno-vpn
    sleep 2

    if systemctl is-active --quiet yadreno-vpn; then
        print_ok "Бот перезапущен и работает!"
    else
        print_err "Бот не запустился после перезаписи"
        echo "  systemctl status yadreno-vpn"
    fi
}

# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================
show_menu() {
    clear
    echo -e "${CYAN}"
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║       🌐 Yadreno VPN Manager         ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo -e "${NC}"
    echo "  1) 🚀 Установка"
    echo "  2) 🔄 Мягкое обновление (git pull)"
    echo "  3) ⚠️  Жёсткая перезапись (с GitHub)"
    echo ""
    echo "  0) Выход"
    echo ""
    read -p "  Выберите действие [0-3]: " choice

    case $choice in
        1) do_install ;;
        2) do_soft_update ;;
        3) do_hard_reset ;;
        0) echo "Пока! 👋"; exit 0 ;;
        *) echo "Неверный выбор"; return 1 ;;
    esac
}

# Проверка root-прав
if [ "$EUID" -ne 0 ]; then
    print_err "Скрипт должен быть запущен от root (sudo)"
    exit 1
fi

show_menu
