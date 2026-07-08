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
LLM_PROVIDER=anthropic
ANTHROPIC_KEY=sk-ant-...
TELEGRAM_TOKEN=123456:ABC...
ASSISTANT_OWNER_ID=...
ANTHROPIC_MODEL=claude-sonnet-5
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
| `LLM_PROVIDER` | — | модель по умолчанию при старте: `anthropic` (по умолчанию), `gigachat`, `yandex`. На лету переключается командой `/model` в боте, выбор сохраняется в `data/settings.json` и переживает перезапуск |
| `TELEGRAM_TOKEN` | да | токен бота от @BotFather |
| `ASSISTANT_OWNER_ID` | — | Telegram ID владельца (ограничение доступа) |
| `GOOGLE_REFRESH_TOKEN` / `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | для Google-инструментов | OAuth для Calendar/Drive/Docs/Sheets/Slides |
| `GOOGLE_CALENDAR_ID` / `GOOGLE_CALENDAR_TZ` | — | по умолчанию `primary` / `Europe/Moscow` |
| `YANDEX_TELEMOST_TOKEN` | для Телемоста | OAuth-токен Yandex |

Ключи модели — только для выбранного `LLM_PROVIDER`:

| Провайдер | Переменные | Заметки |
|---|---|---|
| `anthropic` | `ANTHROPIC_KEY` (обяз.), `ANTHROPIC_MODEL` (по умолч. `claude-sonnet-5`) | единственный провайдер со встроенным веб-поиском (`web_search`/`web_fetch`) |
| `gigachat` | `GIGACHAT_AUTH_KEY` (обяз.), `GIGACHAT_SCOPE` (по умолч. `GIGACHAT_API_PERS`), `GIGACHAT_MODEL` (по умолч. `GigaChat-Max`), `GIGACHAT_CA_BUNDLE` (путь к корневому сертификату Минцифры) | без встроенного веб-поиска; TLS требует CA Минцифры |
| `yandex` | `YANDEX_API_KEY` + `YANDEX_FOLDER_ID` (обяз.), `YANDEX_MODEL` (по умолч. `yandexgpt/latest`) | без встроенного веб-поиска |

> ⚠️ Провайдеры `gigachat` и `yandex` собраны по документированному OpenAI-совместимому
> контракту, но не обкатаны на живом API. Перед боевым переключением прогоните реальные
> сценарии (календарь → свободное окно → Телемост → запись события) и сверьте `base_url`
> и схему авторизации с актуальной докой провайдера. `anthropic` — проверенный путь.

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

Каталог `data/` (`knowledge_base.json`, `chat_memory.json`, `settings.json`)
создаётся автоматически при первом запуске и хранится только на сервере (в
`.gitignore`). `settings.json` помнит выбранного через `/model` провайдера.
Для бэкапа базы знаний копируйте `data/` отдельно.
