# Деплой на VPS (Ubuntu)

Бот работает через **long polling** (`app.run_polling()`), поэтому домен, nginx,
открытые порты и webhook не нужны — достаточно держать запущенным один процесс
через `systemd`.

> ⚠️ Telegram разрешает polling только одному процессу одновременно. Перед запуском
> на VPS остановите бот на старом хостинге, иначе будет ошибка
> `Conflict: terminated by other getUpdates request`.

## 1. Зависимости системы и пользователь для бота

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip git
adduser --system --group --home /opt/akbot akbot
```

## 2. Код и виртуальное окружение

```bash
cd /opt/akbot
sudo -u akbot git clone https://github.com/andreyetoya-blip/ak_claude_bot.git app
cd app
sudo -u akbot python3 -m venv .venv
sudo -u akbot .venv/bin/pip install -r requirements.txt
```

## 3. Переменные окружения

Создайте `/opt/akbot/app/.env` (НЕ коммитится — в `.gitignore`):

```ini
ANTHROPIC_KEY=sk-ant-...
TELEGRAM_TOKEN=123456:ABC...
ASSISTANT_OWNER_ID=...
ANTHROPIC_MODEL=claude-sonnet-4-6
GOOGLE_REFRESH_TOKEN=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_CALENDAR_ID=primary
GOOGLE_CALENDAR_TZ=Europe/Moscow
YANDEX_TELEMOST_TOKEN=...
```

```bash
chmod 600 /opt/akbot/app/.env
```

| Переменная | Обязательна | Назначение |
|---|---|---|
| `ANTHROPIC_KEY` | да | ключ Anthropic API |
| `TELEGRAM_TOKEN` | да | токен бота от @BotFather |
| `ASSISTANT_OWNER_ID` | — | Telegram ID владельца (ограничение доступа) |
| `ANTHROPIC_MODEL` | — | по умолчанию `claude-sonnet-4-6` |
| `GOOGLE_REFRESH_TOKEN` / `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | для Google-инструментов | OAuth для Calendar/Drive/Docs/Sheets/Slides |
| `GOOGLE_CALENDAR_ID` / `GOOGLE_CALENDAR_TZ` | — | по умолчанию `primary` / `Europe/Moscow` |
| `YANDEX_TELEMOST_TOKEN` | для Телемоста | OAuth-токен Yandex |

## 4. systemd-сервис

Файл `akbot.service` лежит в репозитории — скопируйте его:

```bash
cp /opt/akbot/app/akbot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now akbot
systemctl status akbot      # active (running)
journalctl -u akbot -f      # живые логи
```

## 5. Обновление кода

```bash
cd /opt/akbot/app && sudo -u akbot git pull && systemctl restart akbot
```

## Состояние

Каталог `data/` (`knowledge_base.json`, `chat_memory.json`) создаётся
автоматически при первом запуске и хранится только на сервере (в `.gitignore`).
Для бэкапа базы знаний копируйте `data/` отдельно.
