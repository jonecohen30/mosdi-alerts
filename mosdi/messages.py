"""ניסוח ההודעות לטלגרם, בעברית ומימין לשמאל."""
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config as C

RLM = "‏"  # סימן כיוון ימין-לשמאל בתחילת כל שורה

HEB_MONTHS = ["בינואר", "בפברואר", "במרץ", "באפריל", "במאי", "ביוני", "ביולי",
              "באוגוסט", "בספטמבר", "באוקטובר", "בנובמבר", "בדצמבר"]


def rtl(lines: list[str]) -> str:
    return "\n".join((RLM + ln) if ln else "" for ln in lines)


def minutes_he(n: int) -> str:
    return "דקה אחת" if n == 1 else ("שתי דקות" if n == 2 else f"{n} דקות")


def usd(x: float) -> str:
    return f"{x:,.2f}$"


def usd0(x: float) -> str:
    return f"{x:,.0f}$"


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def alert(symbol, mint, grade, mcap_usd, age_sec, prob, ev_per_10, why, risk) -> str:
    g = "א'" if grade == "A" else "ב'"
    mins = max(1, round(age_sec / 60))
    lines = [
        f"<b>הזדמנות חדשה | דרגה {g}</b>",
        "",
        f"מטבע: {esc(symbol)}",
        "כתובת:",
        f"<code>{mint}</code>",
        "",
        f"שווי שוק: {usd0(mcap_usd)}",
        f"זמן מההשקה: {minutes_he(mins)}",
    ]
    if prob is None:
        lines.append("סיכוי להכפלה: עדיין אין מספיק נתונים")
    else:
        lines.append(f"סיכוי להכפלה: {round(prob * 100)} מתוך 100")
        lines.append(f"רווח צפוי: {usd(ev_per_10)} על כל 10$")
    if why:
        lines += ["", "<b>למה:</b>"] + [f"• {w}" for w in why[:4]]
    if risk:
        lines += ["", "<b>סיכון:</b>"] + [f"• {r}" for r in risk[:3]]
    lines += ["", f"כניסה וירטואלית: {usd0(C.STAKE)}"]
    return rtl(lines)


REASON_TEXT = {
    "stop": ["נמכר בסטופ, ירידה של 40% מהכניסה"],
    "trail": ["חצי נמכר בהכפלה", "השאר נמכר בירידה מהשיא"],
    "trail_only": ["נמכר בירידה מהשיא"],
    "timeout": ["נסגר אחרי שעה בלי להגיע ליעד או לסטופ"],
    "timeout_half": ["חצי נמכר בהכפלה", "השאר נסגר אחרי שעה"],
    "stale": ["המטבע הפסיק להיסחר, נסגר במחיר האחרון"],
    "stale_half": ["חצי נמכר בהכפלה", "השאר נסגר במחיר האחרון"],
}


def closed(symbol, stake, exit_value, reason, minutes, prob, balance) -> str:
    pnl = exit_value - stake
    lines = [
        f"<b>עסקה נסגרה | {esc(symbol)}</b>",
        "",
        f"השקעה: {usd(stake)}",
        f"יציאה: {usd(exit_value)}",
        f"{'רווח' if pnl >= 0 else 'הפסד'}: {usd(abs(pnl))}",
        "",
        "<b>איך נסגרה:</b>",
    ] + [f"• {x}" for x in REASON_TEXT.get(reason, [reason])] + [
        "",
        f"משך העסקה: {minutes_he(max(1, round(minutes)))}",
        "המערכת העריכה: " + ("אין מספיק נתונים" if prob is None else f"{round(prob * 100)} מתוך 100"),
        f"יתרה: {usd(balance)}",
    ]
    return rtl(lines)


def daily(now_ts, balance, today: dict, calib: dict, data: dict) -> str:
    d = datetime.fromtimestamp(now_ts, ZoneInfo(C.TZ))
    pnl_total = balance - C.START_BALANCE
    lines = [
        f"<b>דוח יומי | {d.day} {HEB_MONTHS[d.month - 1]}</b>",
        "",
        f"יתרה: {usd(balance)}",
        f"{'רווח' if pnl_total >= 0 else 'הפסד'} מתחילת הדרך: {usd(abs(pnl_total))}",
        "",
        f"עסקאות היום: {today['n']}",
        f"הצליחו: {today['wins']}",
        f"נעצרו בסטופ: {today['stops']}",
        f"נסגרו בזמן: {today['timeouts']}",
        f"{'רווח' if today['pnl'] >= 0 else 'הפסד'} היום: {usd(abs(today['pnl']))}",
        "",
        "<b>בדיקת אמינות:</b>",
    ]
    if calib["n"] < C.MIN_SAMPLES_FOR_PROB:
        lines.append(f"עדיין אוספים נתונים: {calib['n']} מתוך {C.MIN_SAMPLES_FOR_PROB} עסקאות שנדרשות")
    else:
        lines.append(f"המערכת העריכה בממוצע: {round(calib['pred'] * 100)} מתוך 100")
        lines.append(f"בפועל הצליחו: {round(calib['actual'] * 100)} מתוך 100")
    lines += [
        "",
        "<b>איסוף נתונים:</b>",
        f"מטבעות חדשים היום: {data['tokens']:,}",
        f"עסקאות שנשמרו היום: {data['trades']:,}",
        f"מטבעות שעברו נקודת בדיקה: {data['checkpoints']:,}",
        f"מתוכם נפסלו: {data['rejected']:,}",
    ]
    return rtl(lines)


def system(text: str) -> str:
    return rtl([text])
