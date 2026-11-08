"""מוצא את מספר הצ'אט של מי ששלח הודעה לבוט, ושומר אותו בקובץ .env.

קודי יציאה: 0 הצלחה, 2 אין הודעה עדיין, 3 טוקן שגוי, 4 בעיית רשת.
"""
import json
import sys
import urllib.error
import urllib.request

from . import config as C


def call(method: str) -> dict:
    url = f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN}/{method}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def main() -> int:
    if not C.TELEGRAM_TOKEN:
        print("BAD_TOKEN: missing")
        return 3
    try:
        me = call("getMe")["result"]["username"]
        call("deleteWebhook")
        data = call("getUpdates")
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            print("BAD_TOKEN")
            return 3
        print(f"NETWORK: HTTP {e.code}")
        return 4
    except Exception as e:  # noqa: BLE001
        print(f"NETWORK: {e}")
        return 4
    chats = [u["message"]["chat"]["id"] for u in data.get("result", []) if "message" in u]
    if not chats:
        print(f"NO_MESSAGE: send any message to @{me} in Telegram")
        return 2
    chat_id = chats[-1]
    lines = [ln for ln in C.ENV_PATH.read_text(encoding="utf-8").splitlines()
             if not ln.startswith("TELEGRAM_CHAT_ID=")]
    lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
    C.ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"OK: connected to @{me}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
