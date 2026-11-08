"""המנוע: האזנה, שמירה, נקודות בדיקה, מעקב תוצאות ועסקאות נייר."""
import asyncio
import json
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import websockets

from . import config as C
from . import messages as M
from .db import DB
from .scoring import TokenState, features, grade_of, reject_reasons, score

log = logging.getLogger("mosdi")


def local_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(C.TZ)).strftime("%Y-%m-%d")


class Shadow:
    """מעקב אחרי תוצאה של נקודת בדיקה: האם הגיע ל-+100% לפני 40%-, בתוך שעה."""
    __slots__ = ("cid", "cp_ts", "entry", "hi", "lo")

    def __init__(self, cid, cp_ts):
        self.cid, self.cp_ts, self.entry, self.hi, self.lo = cid, cp_ts, None, 1.0, 1.0


class Paper:
    __slots__ = ("pid", "mint", "symbol", "alert_ts", "entry_ts", "entry", "prob", "grade",
                 "half", "half_value", "peak", "last_mcap", "last_ts")

    def __init__(self, pid, mint, symbol, alert_ts, prob, grade):
        self.pid, self.mint, self.symbol, self.alert_ts = pid, mint, symbol, alert_ts
        self.prob, self.grade = prob, grade
        self.entry_ts = self.entry = None
        self.half, self.half_value = False, 0.0
        self.peak = self.last_mcap = 0.0
        self.last_ts = alert_ts


class Engine:
    def __init__(self):
        self.db = DB(C.DB_PATH)
        self.tokens: dict[str, TokenState] = {}
        self.shadows: dict[str, list[Shadow]] = {}
        self.papers: dict[str, Paper] = {}
        self.sub_queue: list[str] = []
        self.unsub_queue: list[str] = []
        self.ws = None
        self.sol_usd: float | None = None
        self.last_msg_ts = time.time()
        self.tg_queue: asyncio.Queue = asyncio.Queue()
        self.http: aiohttp.ClientSession | None = None
        self.balance = self.db.get("balance", C.START_BALANCE)
        self.day = local_date(time.time())
        self.counters = self.db.get("counters:" + self.day, {"tokens": 0, "trades": 0})

    # ------------------------------------------------------------------ טלגרם
    async def tg_sender(self):
        while True:
            text = await self.tg_queue.get()
            if not (C.TELEGRAM_TOKEN and C.TELEGRAM_CHAT_ID):
                log.info("TG(no config): %s", text[:200])
                continue
            url = f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": C.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                       "disable_web_page_preview": True}
            for attempt in range(3):
                try:
                    async with self.http.post(url, json=payload, timeout=15) as r:
                        if r.status == 200:
                            break
                        log.warning("telegram %s: %s", r.status, await r.text())
                        if r.status == 429:
                            await asyncio.sleep(5)
                except Exception as e:  # noqa: BLE001
                    log.warning("telegram error: %s", e)
                    await asyncio.sleep(2)
            await asyncio.sleep(1.1)

    def send(self, text: str):
        self.tg_queue.put_nowait(text)

    # ------------------------------------------------------------------ מחיר סולאנה
    async def sol_price_loop(self):
        while True:
            try:
                async with self.http.get(C.SOL_PRICE_URL, timeout=10) as r:
                    j = await r.json()
                    self.sol_usd = float(j["data"]["amount"])
            except Exception as e:  # noqa: BLE001
                log.warning("sol price error: %s", e)
            await asyncio.sleep(60 if self.sol_usd else 5)

    # ------------------------------------------------------------------ זרם נתונים
    async def stream_loop(self):
        backoff = 1
        while True:
            try:
                async with websockets.connect(C.PUMPPORTAL_WS, ping_interval=20, max_size=2**22) as ws:
                    self.ws = ws
                    backoff = 1
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    active = list(self.tokens.keys())
                    for i in range(0, len(active), 200):
                        await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": active[i:i + 200]}))
                    log.info("connected, resubscribed %d tokens", len(active))
                    async for raw in ws:
                        self.last_msg_ts = time.time()
                        try:
                            self.on_message(json.loads(raw))
                        except Exception:  # noqa: BLE001
                            log.exception("message handling failed")
            except Exception as e:  # noqa: BLE001
                log.warning("stream disconnected: %s", e)
            self.ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def sub_loop(self):
        while True:
            await asyncio.sleep(1)
            if self.ws is None:
                continue
            try:
                if self.sub_queue:
                    keys, self.sub_queue = self.sub_queue[:200], self.sub_queue[200:]
                    await self.ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": keys}))
                if self.unsub_queue:
                    keys, self.unsub_queue = self.unsub_queue[:200], self.unsub_queue[200:]
                    await self.ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": keys}))
            except Exception as e:  # noqa: BLE001
                log.warning("subscribe error: %s", e)

    # ------------------------------------------------------------------ עיבוד הודעה
    def on_message(self, m: dict):
        mint = m.get("mint")
        if not mint:
            return
        now = time.time()
        tx = m.get("txType")
        if tx == "create":
            self.on_create(m, now)
        elif tx in ("buy", "sell"):
            self.on_trade(m, now)
        elif tx == "migrate" or (tx is None and m.get("pool")):
            self.db.mark_migrated(mint, now)
            t = self.tokens.get(mint)
            if t:
                t.migrated = True

    def on_create(self, m, now):
        mint = m["mint"]
        if mint in self.tokens:
            return
        t = TokenState(mint=mint, name=m.get("name", ""), symbol=m.get("symbol", ""),
                       uri=m.get("uri", ""), creator=m.get("traderPublicKey", ""), created_ts=now)
        self.tokens[mint] = t
        self.db.add_token(mint, t.name, t.symbol, t.uri, t.creator, now)
        self.counters["tokens"] += 1
        self.sub_queue.append(mint)
        init_tokens = float(m.get("initialBuy") or 0)
        if init_tokens > 0:
            self.on_trade({"mint": mint, "traderPublicKey": t.creator, "txType": "buy",
                           "solAmount": m.get("solAmount", 0), "tokenAmount": init_tokens,
                           "vSolInBondingCurve": m.get("vSolInBondingCurve"),
                           "marketCapSol": m.get("marketCapSol"), "pool": m.get("pool", "pump")}, now)

    def on_trade(self, m, now):
        mint = m["mint"]
        t = self.tokens.get(mint)
        if t is None:
            return
        side = m["txType"]
        sol = float(m.get("solAmount") or 0)
        tok = float(m.get("tokenAmount") or 0)
        vsol = float(m.get("vSolInBondingCurve") or 0)
        mcap = float(m.get("marketCapSol") or 0)
        pool = m.get("pool", "pump")
        trader = m.get("traderPublicKey", "")
        t.apply(now, trader, side, sol, tok, vsol, mcap)
        self.db.add_trade((now, mint, trader, side, sol, tok, vsol, mcap, pool))
        self.counters["trades"] += 1

        price = t.mcap_sol
        if price:
            for sh in list(self.shadows.get(mint, [])):
                self.update_shadow(mint, sh, now, price)
            p = self.papers.get(mint)
            if p:
                self.update_paper(p, now, price)

        if not t.migrated and pool == "pump":
            for cp in C.CHECKPOINTS:
                if cp not in t.cps_hit and t.vsol >= cp:
                    t.cps_hit.add(cp)
                    self.on_checkpoint(t, cp, now)

    # ------------------------------------------------------------------ נקודת בדיקה
    def on_checkpoint(self, t: TokenState, cp: float, now: float):
        hist = self.db.creator_history(t.creator, now)
        f = features(t, now, hist)
        rej = reject_reasons(f)
        s, why, risk = score(f)
        g = grade_of(s)
        cid = self.db.add_checkpoint(t.mint, cp, now, t.mcap_sol, s, g, bool(rej), rej, f)
        self.shadows.setdefault(t.mint, []).append(Shadow(cid, now))

        if rej or g != "A":
            return
        if t.mint in self.papers or len(self.papers) >= C.MAX_OPEN:
            return
        if self.balance < C.STAKE or self.today_pnl(now) <= -C.DAILY_LOSS_STOP:
            return
        if not self.sol_usd:
            return

        n, hits = self.db.hit_rate(cp, g, now, C.PROB_LOOKBACK_DAYS)
        prob = (hits / n) if n >= C.MIN_SAMPLES_FOR_PROB else None
        ev10 = None
        if prob is not None:
            cost = 2 * (C.FEE_PER_SIDE + C.SLIPPAGE_PER_SIDE)
            ev10 = C.STAKE * (prob * C.TAKE_PROFIT + (1 - prob) * C.STOP_LOSS - cost)
        cur = self.db.conn.execute(
            """INSERT INTO paper_trades(checkpoint_id,mint,symbol,alert_ts,prob,grade,stake,status)
               VALUES (?,?,?,?,?,?,?, 'pending')""",
            (cid, t.mint, t.symbol, now, prob, g, C.STAKE))
        self.papers[t.mint] = Paper(cur.lastrowid, t.mint, t.symbol, now, prob, g)
        self.send(M.alert(t.symbol, t.mint, g, t.mcap_sol * self.sol_usd, f["age_sec"], prob, ev10, why, risk))

    def update_shadow(self, mint: str, sh: Shadow, now: float, price: float):
        if sh.entry is None:
            if now - sh.cp_ts >= C.ENTRY_DELAY_SEC:
                sh.entry = price
            return
        mult = price / sh.entry
        sh.hi, sh.lo = max(sh.hi, mult), min(sh.lo, mult)
        if mult >= 1 + C.TAKE_PROFIT:
            self.close_shadow(mint, sh, "target", now)
        elif mult <= 1 + C.STOP_LOSS:
            self.close_shadow(mint, sh, "stop", now)

    def close_shadow(self, mint: str, sh: Shadow, outcome: str, now: float):
        self.db.resolve_checkpoint(sh.cid, outcome, sh.hi, sh.lo, now)
        lst = self.shadows.get(mint)
        if lst and sh in lst:
            lst.remove(sh)

    # ------------------------------------------------------------------ עסקת נייר
    def value(self, p: Paper, frac: float, price: float) -> float:
        entry_eff = p.entry * (1 + C.FEE_PER_SIDE + C.SLIPPAGE_PER_SIDE)
        return C.STAKE * frac * (price / entry_eff) * (1 - C.FEE_PER_SIDE - C.SLIPPAGE_PER_SIDE)

    def update_paper(self, p: Paper, now: float, price: float):
        p.last_mcap, p.last_ts = price, now
        if p.entry is None:
            if now - p.alert_ts >= C.ENTRY_DELAY_SEC:
                p.entry, p.entry_ts, p.peak = price, now, price
                self.balance -= C.STAKE
                self.db.set("balance", self.balance)
                self.db.conn.execute(
                    "UPDATE paper_trades SET entry_ts=?, entry_mcap=?, status='open' WHERE id=?",
                    (now, price, p.pid))
            return
        p.peak = max(p.peak, price)
        mult = price / p.entry
        if not p.half:
            if mult >= 1 + C.TAKE_PROFIT:
                p.half, p.half_value = True, self.value(p, 0.5, price)
                self.db.conn.execute("UPDATE paper_trades SET half_sold=1, half_value=? WHERE id=?",
                                     (p.half_value, p.pid))
            elif mult <= 1 + C.STOP_LOSS:
                self.close_paper(p, now, self.value(p, 1.0, price), "stop")
        else:
            if price <= p.peak * (1 - C.TRAIL_FROM_PEAK):
                self.close_paper(p, now, p.half_value + self.value(p, 0.5, price), "trail")

    def close_paper(self, p: Paper, now: float, exit_value: float, reason: str):
        self.balance += exit_value
        self.db.set("balance", self.balance)
        self.db.conn.execute(
            """UPDATE paper_trades SET exit_ts=?, exit_value=?, exit_reason=?, peak_mcap=?, status='closed'
               WHERE id=?""", (now, exit_value, reason, p.peak, p.pid))
        self.papers.pop(p.mint, None)
        minutes = (now - (p.entry_ts or p.alert_ts)) / 60
        self.send(M.closed(p.symbol, C.STAKE, exit_value, reason, minutes, p.prob, self.balance))

    def today_pnl(self, now: float) -> float:
        start = datetime.fromtimestamp(now, ZoneInfo(C.TZ)).replace(hour=0, minute=0, second=0).timestamp()
        r = self.db.conn.execute(
            "SELECT COALESCE(SUM(exit_value - stake),0) FROM paper_trades WHERE status='closed' AND exit_ts>=?",
            (start,)).fetchone()
        return r[0]

    # ------------------------------------------------------------------ תחזוקה
    async def housekeeping_loop(self):
        alerted_down = False
        while True:
            await asyncio.sleep(5)
            now = time.time()
            self.db.flush()

            # זמן מקסימלי ותקיעות בעסקאות נייר
            for p in list(self.papers.values()):
                if p.entry is None:
                    if now - p.alert_ts > 10 * 60:   # לא הייתה עסקה אחרי ההתראה
                        self.db.conn.execute("UPDATE paper_trades SET status='cancelled' WHERE id=?", (p.pid,))
                        self.papers.pop(p.mint, None)
                    continue
                if now - p.entry_ts >= C.MAX_HOLD_SEC:
                    rest = 0.5 if p.half else 1.0
                    self.close_paper(p, now, p.half_value + self.value(p, rest, p.last_mcap),
                                     "timeout_half" if p.half else "timeout")
                elif now - p.last_ts >= C.INACTIVE_UNSUB_SEC:
                    rest = 0.5 if p.half else 1.0
                    self.close_paper(p, now, p.half_value + self.value(p, rest, p.last_mcap),
                                     "stale_half" if p.half else "stale")

            # זמן מקסימלי במעקב תוצאות
            for mint, lst in list(self.shadows.items()):
                for sh in list(lst):
                    if now - sh.cp_ts >= C.MAX_HOLD_SEC:
                        self.close_shadow(mint, sh, "timeout", now)
            self.shadows = {k: v for k, v in self.shadows.items() if v}

            # ניקוי מטבעות לא פעילים
            for mint, t in list(self.tokens.items()):
                idle = now - (t.last_ts or t.created_ts)
                if idle >= C.INACTIVE_UNSUB_SEC and mint not in self.shadows and mint not in self.papers:
                    del self.tokens[mint]
                    self.unsub_queue.append(mint)

            # מונים ודוח יומי
            today = local_date(now)
            self.db.set("counters:" + self.day, self.counters)
            if today != self.day:
                self.day, self.counters = today, {"tokens": 0, "trades": 0}
            hour = datetime.fromtimestamp(now, ZoneInfo(C.TZ)).hour
            if hour >= C.DAILY_REPORT_HOUR and self.db.get("last_report") != today:
                self.db.set("last_report", today)
                self.send(self.daily_report(now))

            # בריאות הזרם
            silent = now - self.last_msg_ts
            if silent > 120 and self.ws is not None:
                log.warning("no data for %ds, reconnecting", silent)
                await self.ws.close()
            if silent > 600 and not alerted_down:
                alerted_down = True
                self.send(M.system("התראה: לא מתקבלים נתונים כבר 10 דקות. המערכת מנסה להתחבר מחדש."))
            elif silent < 60 and alerted_down:
                alerted_down = False
                self.send(M.system("הנתונים חזרו להתקבל."))

    def daily_report(self, now: float) -> str:
        start = datetime.fromtimestamp(now, ZoneInfo(C.TZ)).replace(hour=0, minute=0, second=0).timestamp()
        q = self.db.conn.execute
        rows = q("SELECT exit_value, stake, exit_reason, half_sold FROM paper_trades "
                 "WHERE status='closed' AND exit_ts>=?", (start,)).fetchall()
        today = {
            "n": len(rows),
            "wins": sum(1 for r in rows if r["exit_value"] > r["stake"]),
            "stops": sum(1 for r in rows if r["exit_reason"] == "stop"),
            "timeouts": sum(1 for r in rows if (r["exit_reason"] or "").startswith(("timeout", "stale"))),
            "pnl": sum(r["exit_value"] - r["stake"] for r in rows),
        }
        c = q("SELECT COUNT(*) n, AVG(prob) pred, AVG(half_sold) actual FROM paper_trades "
              "WHERE status='closed' AND prob IS NOT NULL").fetchone()
        if (c["n"] or 0) >= C.MIN_SAMPLES_FOR_PROB:
            calib = {"n": c["n"], "pred": c["pred"], "actual": c["actual"]}
        else:
            base = q("SELECT COUNT(*) FROM checkpoints WHERE grade='A' AND rejected=0 AND outcome IS NOT NULL"
                     ).fetchone()[0]
            calib = {"n": base, "pred": 0, "actual": 0}
        cps = q("SELECT COUNT(*) n, SUM(rejected) r FROM checkpoints WHERE ts>=?", (start,)).fetchone()
        data = {"tokens": self.counters["tokens"], "trades": self.counters["trades"],
                "checkpoints": cps["n"] or 0, "rejected": cps["r"] or 0}
        return M.daily(now, self.balance + C.STAKE * sum(1 for p in self.papers.values() if p.entry),
                       today, calib, data)

    # ------------------------------------------------------------------ הפעלה
    async def run(self):
        async with aiohttp.ClientSession() as http:
            self.http = http
            self.send(M.system("המערכת עלתה ומתחילה לאסוף נתונים. תיק נייר: 1,000$, 10$ לעסקה."))
            await asyncio.gather(self.tg_sender(), self.sol_price_loop(), self.stream_loop(),
                                 self.sub_loop(), self.housekeeping_loop(), self.tg_commands_loop())

    # ------------------------------------------------------------------ פקודות מטלגרם
    async def tg_commands_loop(self):
        if not (C.TELEGRAM_TOKEN and C.TELEGRAM_CHAT_ID):
            return
        url = f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN}/getUpdates"
        offset = self.db.get("tg_offset", 0)
        while True:
            try:
                async with self.http.get(url, params={"offset": offset, "timeout": 50},
                                         timeout=aiohttp.ClientTimeout(total=70)) as r:
                    data = await r.json()
                for u in data.get("result", []):
                    offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if str(msg.get("chat", {}).get("id")) != str(C.TELEGRAM_CHAT_ID):
                        continue
                    text = (msg.get("text") or "").strip().lower()
                    if text in ("סטטוס", "/status", "status", "מצב"):
                        self.send(self.status_report(time.time()))
                self.db.set("tg_offset", offset)
            except Exception as e:  # noqa: BLE001
                log.warning("telegram poll error: %s", e)
                await asyncio.sleep(5)

    def status_report(self, now: float) -> str:
        q = self.db.conn.execute
        self.db.flush()
        hour = now - 3600
        start = datetime.fromtimestamp(now, ZoneInfo(C.TZ)).replace(hour=0, minute=0, second=0).timestamp()
        tok_h = q("SELECT COUNT(*) FROM tokens WHERE created_ts>=?", (hour,)).fetchone()[0]
        trd_h = q("SELECT COUNT(*) FROM trades WHERE ts>=?", (hour,)).fetchone()[0]
        cps = q("SELECT grade, rejected, COUNT(*) n FROM checkpoints WHERE ts>=? GROUP BY 1,2", (start,)).fetchall()
        by = {(r["grade"], r["rejected"]): r["n"] for r in cps}
        reasons = {}
        for (rs,) in q("SELECT reasons FROM checkpoints WHERE ts>=? AND rejected=1", (start,)).fetchall():
            for x in json.loads(rs or "[]"):
                reasons[x] = reasons.get(x, 0) + 1
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:4]
        silent = int(now - self.last_msg_ts)
        lines = [
            "<b>סטטוס מערכת</b>",
            "",
            f"חיבור לנתונים: {'תקין' if silent < 60 else f'אין נתונים כבר {silent} שניות'}",
            f"מטבעות במעקב כרגע: {len(self.tokens):,}",
            f"מטבעות חדשים בשעה האחרונה: {tok_h:,}",
            f"עסקאות בשעה האחרונה: {trd_h:,}",
            "",
            "<b>נקודות בדיקה היום:</b>",
            f"דרגה א': {by.get(('A', 0), 0)} עברו, {by.get(('A', 1), 0)} נפסלו",
            f"דרגה ב': {by.get(('B', 0), 0)} עברו, {by.get(('B', 1), 0)} נפסלו",
            f"דרגה ג': {by.get(('C', 0), 0)} עברו, {by.get(('C', 1), 0)} נפסלו",
        ]
        if top:
            lines += ["", "<b>סיבות פסילה נפוצות היום:</b>"] + [f"• {k}: {v}" for k, v in top]
        lines += ["", f"עסקאות נייר פתוחות: {len(self.papers)}",
                  f"יתרה: {M.usd(self.balance + C.STAKE * sum(1 for p in self.papers.values() if p.entry))}"]
        return M.rtl(lines)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(Engine().run())


if __name__ == "__main__":
    main()
