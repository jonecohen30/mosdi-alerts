"""הגדרות המערכת. ערכים סודיים נקראים מקובץ .env בשרת בלבד."""
import os
from pathlib import Path

BASE_DIR = Path(os.environ.get("MOSDI_HOME", "/opt/mosdi"))
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "mosdi.db"
ENV_PATH = BASE_DIR / ".env"


def _load_env() -> None:
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# מקור הנתונים
PUMPPORTAL_WS = os.environ.get("PUMPPORTAL_WS", "wss://pumpportal.fun/api/data")
SOL_PRICE_URL = "https://api.coinbase.com/v2/prices/SOL-USD/spot"

# מכניקת העקומה
VSOL_START = 30.0          # SOL וירטואליים בהשקה
VSOL_GRADUATION = 115.0    # בוגרות
TOTAL_SUPPLY = 1_000_000_000

# נקודות בדיקה: SOL וירטואליים בעקומה
CHECKPOINTS = [55.0, 70.0, 85.0]

# תיק וירטואלי
START_BALANCE = 1000.0
STAKE = 10.0
MAX_OPEN = 5
DAILY_LOSS_STOP = 50.0      # 5% מהתיק ההתחלתי

# כללי יציאה
ENTRY_DELAY_SEC = 2.0
STOP_LOSS = -0.40
TAKE_PROFIT = 1.00          # +100% → חצי יוצא
TRAIL_FROM_PEAK = 0.30
MAX_HOLD_SEC = 60 * 60

# עלויות לכל כיוון: עמלת עקומה + החלקה מוערכת
FEE_PER_SIDE = 0.0125
SLIPPAGE_PER_SIDE = 0.01

# ציון
GRADE_A = 70
GRADE_B = 50
MIN_SAMPLES_FOR_PROB = 30
PROB_LOOKBACK_DAYS = 14

# תחזוקה
INACTIVE_UNSUB_SEC = 15 * 60
DAILY_REPORT_HOUR = 21      # שעון ישראל
TZ = "Asia/Jerusalem"
