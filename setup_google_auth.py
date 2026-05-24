"""
Одноразовый локальный скрипт: получить refresh token Google Calendar.

Использование:
    python3 setup_google_auth.py path/to/credentials.json

Скрипт откроет браузер, попросит разрешение на доступ к календарю,
и распечатает три значения для переменных окружения на render.com:
    GOOGLE_CLIENT_ID
    GOOGLE_CLIENT_SECRET
    GOOGLE_REFRESH_TOKEN
"""

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main() -> None:
    if len(sys.argv) != 2:
        print("Использование: python3 setup_google_auth.py path/to/credentials.json")
        sys.exit(1)

    credentials_path = Path(sys.argv[1])
    if not credentials_path.exists():
        print(f"Файл не найден: {credentials_path}")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    if not creds.refresh_token:
        print("Не получили refresh_token. Удали приложение из https://myaccount.google.com/permissions и запусти снова.")
        sys.exit(1)

    raw = json.loads(credentials_path.read_text())
    section = raw.get("installed") or raw.get("web") or {}
    client_id = section.get("client_id", creds.client_id)
    client_secret = section.get("client_secret", creds.client_secret)

    print()
    print("Готово. Добавь эти переменные в Environment на render.com:")
    print()
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print()


if __name__ == "__main__":
    main()
