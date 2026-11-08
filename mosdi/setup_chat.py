"""מוצא את מספר הצ'אט של מי ששלח הודעה לבוט, ושומר אותו בקובץ .env."""
import json
import sys
import urllib.request

from . import config as C


def main() -> int:
    if not C.TELEGRAM_TOKEN:
        print("חסר טוקן של טלגרם")
        return 1
    url = f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN}/getUpdates"
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.load(r)
    chats = [u["message"]["chat"]["id"] for u in data.get("result", []) if "message" in u]
    if not chats:
        print("לא נמצאה הודעה. שלח לבוט 'שלום' בטלגרם והרץ שוב.")
        return 2
    chat_id = chats[-1]
    lines = [ln for ln in C.ENV_PATH.read_text(encoding="utf-8").splitlines()
             if not ln.startswith("TELEGRAM_CHAT_ID=")]
    lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
    C.ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("הצ'אט נשמר.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
