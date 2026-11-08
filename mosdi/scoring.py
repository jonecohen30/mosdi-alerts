"""מצב חי של כל מטבע, חישוב משתנים, פסילה וציון.

גרסה 1: כללים ידניים מהמסמך. אחרי איסוף נתונים הספים ייקבעו מחדש לפי מה שבאמת עבד.
"""
from collections import Counter, deque
from dataclasses import dataclass, field

from . import config as C


@dataclass
class TokenState:
    mint: str
    name: str
    symbol: str
    uri: str
    creator: str
    created_ts: float
    trades: int = 0
    buys: int = 0
    sells: int = 0
    vsol: float = C.VSOL_START
    mcap_sol: float = 0.0
    last_ts: float = 0.0
    migrated: bool = False
    balances: dict = field(default_factory=dict)        # ארנק → טוקנים
    buyers: set = field(default_factory=set)
    early_wallets: set = field(default_factory=set)     # קנו ב-3 השניות הראשונות
    early_tokens: float = 0.0
    creator_bought: float = 0.0
    creator_sold: float = 0.0
    buy_sizes: Counter = field(default_factory=Counter)
    recent: deque = field(default_factory=lambda: deque(maxlen=400))  # (ts, side, sol, mcap)
    cps_hit: set = field(default_factory=set)

    def apply(self, ts, trader, side, sol, tokens, vsol, mcap):
        self.trades += 1
        self.last_ts = ts
        if vsol:
            self.vsol = vsol
        if mcap:
            self.mcap_sol = mcap
        b = self.balances.get(trader, 0.0)
        if side == "buy":
            self.buys += 1
            self.buyers.add(trader)
            self.balances[trader] = b + tokens
            self.buy_sizes[round(sol, 3)] += 1
            if ts - self.created_ts <= 3:
                self.early_wallets.add(trader)
                self.early_tokens += tokens
            if trader == self.creator:
                self.creator_bought += tokens
        else:
            self.sells += 1
            self.balances[trader] = max(0.0, b - tokens)
            if trader == self.creator:
                self.creator_sold += tokens
        self.recent.append((ts, side, sol, mcap or self.mcap_sol))


def features(t: TokenState, now: float, creator_hist: dict) -> dict:
    real_sol = max(0.0, t.vsol - C.VSOL_START)
    holders = sorted((v for v in t.balances.values() if v > 0), reverse=True)
    top10 = sum(holders[:10]) / C.TOTAL_SUPPLY
    early_hold = sum(t.balances.get(w, 0.0) for w in t.early_wallets) / C.TOTAL_SUPPLY
    same_size = (t.buy_sizes.most_common(1)[0][1] / t.buys) if t.buys else 0.0
    buy60 = sum(s for (ts, side, s, _) in t.recent if side == "buy" and now - ts <= 60)
    sell60 = sum(s for (ts, side, s, _) in t.recent if side == "sell" and now - ts <= 60)
    mcaps = [m for (_, _, _, m) in t.recent if m]
    peak = max(mcaps) if mcaps else t.mcap_sol
    drawdown = (t.mcap_sol / peak - 1) if peak else 0.0
    creator_sold_frac = (t.creator_sold / t.creator_bought) if t.creator_bought else 0.0
    return {
        "age_sec": round(now - t.created_ts, 1),
        "trades": t.trades,
        "real_sol": round(real_sol, 2),
        "sol_per_trade": round(real_sol / t.trades, 4) if t.trades else 0.0,
        "unique_buyers": len(t.buyers),
        "top10": round(top10, 4),
        "early_hold": round(early_hold, 4),
        "same_size_frac": round(same_size, 3),
        "flow60": round(buy60 / sell60, 2) if sell60 else (99.0 if buy60 else 0.0),
        "drawdown": round(drawdown, 3),
        "creator_sold_frac": round(creator_sold_frac, 3),
        "has_uri": bool(t.uri),
        "creator_launches": creator_hist["launches"],
        "creator_grads": creator_hist["grads"],
        "creator_launches_24h": creator_hist["launches_24h"],
    }


def reject_reasons(f: dict) -> list[str]:
    r = []
    if not f["has_uri"]:
        r.append("אין פרטי מטבע (תמונה ותיאור)")
    if f["top10"] > 0.50:
        r.append("10 המחזיקים הגדולים מחזיקים יותר מחצי")
    if f["early_hold"] > 0.30:
        r.append("קונים מהשניות הראשונות מחזיקים יותר מ-30%")
    if f["creator_sold_frac"] > 0.50:
        r.append("היוצר כבר מכר יותר מחצי")
    if f["creator_launches_24h"] >= 5 and f["creator_grads"] == 0:
        r.append("יוצר סדרתי בלי אף הצלחה")
    if f["drawdown"] < -0.30:
        r.append("ירידה חדה מהשיא לפני הכניסה")
    if f["age_sec"] > 30 * 60:
        r.append("התקדמות איטית מדי")
    return r


def score(f: dict) -> tuple[int, list[str], list[str]]:
    """מחזיר ציון 0–100, סיבות חיוביות וסיכונים, בעברית לתצוגה."""
    s, why, risk = 0, [], []

    spt = f["sol_per_trade"]
    if spt >= 0.5:
        s += 30; why.append("כסף נכנס מהר מאוד, בעסקאות גדולות")
    elif spt >= 0.3:
        s += 20; why.append("כסף נכנס מהר")
    elif spt >= 0.15:
        s += 10

    age = f["age_sec"]
    if age <= 120:
        s += 15; why.append(f"הגיע לשלב הזה תוך {int(age)} שניות")
    elif age <= 300:
        s += 10
    elif age <= 600:
        s += 5

    ss = f["same_size_frac"]
    if ss <= 0.20:
        s += 15; why.append("גדלי קנייה מגוונים, נראה כמו אנשים ולא בוטים")
    elif ss <= 0.35:
        s += 8
    else:
        risk.append("הרבה קניות באותו גודל, סימן לבוטים")

    ub = f["unique_buyers"]
    if ub >= 40:
        s += 10; why.append(f"{ub} קונים שונים")
    elif ub >= 20:
        s += 5

    fl = f["flow60"]
    if fl >= 1.5:
        s += 15; why.append("בדקה האחרונה קונים יותר ממוכרים")
    elif fl >= 1.0:
        s += 8
    else:
        risk.append("בדקה האחרונה מוכרים יותר מקונים")

    t10 = f["top10"]
    if t10 <= 0.30:
        s += 15
    elif t10 <= 0.40:
        s += 8
    else:
        risk.append(f"10 המחזיקים הגדולים מחזיקים {round(t10 * 100)} מתוך 100")

    if f["creator_launches"] >= 3 and f["creator_grads"] == 0:
        risk.append(f"ליוצר {f['creator_launches']} השקות קודמות בלי אף הצלחה")
    elif f["creator_grads"] > 0:
        why.append(f"ליוצר {f['creator_grads']} מטבעות שהצליחו בעבר")

    return min(s, 100), why, risk


def grade_of(s: int) -> str:
    return "A" if s >= C.GRADE_A else ("B" if s >= C.GRADE_B else "C")
