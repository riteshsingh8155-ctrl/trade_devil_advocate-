import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Live")

def _run_keep_alive():
    import os
    port=int(os.environ.get("PORT",10000))
    HTTPServer(("0.0.0.0",port),KeepAliveHandler).serve_forever()

threading.Thread(target=_run_keep_alive, daemon=True).start()

import os, sys, time, sqlite3
import pandas as pd, pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect

load_dotenv()

CHARTER = {  # genesis prompts: each agent has ONE job
    "Data": "Fetch clean candles for 1m..1w from Angel One. Nothing else.",
    "Indicator": "Compute EMA 5/9/13/18/24/34/50/200, RSI, MACD. Report bias only.",
    "SMC": "Find structure, BOS, liquidity sweeps, FVG, order blocks, trap zones.",
    "Devil": "Argue against the trade. Veto if a trap or higher-timeframe conflict exists.",
    "Risk": "Allow a trade ONLY if reward:risk >= 1:3. Size by risk % of capital.",
    "Reporter": "Alert (text and voice), log to journal. Never place orders.",
}
# Verify tokens in Angel One's instrument master. MCX contracts change monthly: fill yours in.
WATCH = {"NIFTY": ("NSE", "99926000"), "BANKNIFTY": ("NSE", "99926009"), "SENSEX": ("BSE", "99919000")}
# "CRUDEOIL": ("MCX", "<token>"), "GOLD": ("MCX", "<token>"), "SILVER": ("MCX", "<token>")
TFS = {"1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE", "1h": "ONE_HOUR", "1d": "ONE_DAY"}
DAYS = {"1m": 2, "5m": 5, "15m": 10, "1h": 30, "1d": 400}
TW = {"1m": 1, "5m": 1.5, "15m": 2, "1h": 3, "1d": 4, "1w": 5}
EMAS = [5, 9, 13, 18, 24, 34, 50, 200]
MIN_RR = float(os.getenv("MIN_RR", "3"))          # 1:3 gate
CAPITAL = float(os.getenv("CAPITAL", "100000"))
RISK_PCT = float(os.getenv("RISK_PCT", "1"))
EVERY = int(os.getenv("SCAN_SECONDS", "300"))


class DataAgent:
    def __init__(s):
        s.api = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
        totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
        r = s.api.generateSession(os.environ["ANGEL_CLIENT_CODE"], os.environ["ANGEL_PIN"], totp)
        if not r.get("status"):
            sys.exit("Login failed: " + str(r.get("message")))

    def candles(s, ex, tok, tf, days=None):
        now = pd.Timestamp.now()
        frm = now - pd.Timedelta(days=days or DAYS[tf])
        r = s.api.getCandleData({"exchange": ex, "symboltoken": tok, "interval": TFS[tf],
                                 "fromdate": frm.strftime("%Y-%m-%d %H:%M"), "todate": now.strftime("%Y-%m-%d %H:%M")})
        time.sleep(0.4)  # stay under API rate limits
        return pd.DataFrame(r.get("data") or [], columns=["t", "o", "h", "l", "c", "v"])

    @staticmethod
    def weekly(d1):
        x = d1.copy()
        x.index = pd.to_datetime(x.t, utc=True)
        return x.resample("W").agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna().reset_index(drop=True)


def sgn(x, a=0):
    return 1 if x > a else -1 if x < -a else 0


def indicator_agent(df):
    c = df.c.astype(float)
    ev = sorted((p, c.ewm(span=p, adjust=False).mean().iloc[-1]) for p in EMAS if len(c) >= p)
    vals = [v for _, v in ev]
    ema = 1 if len(vals) > 1 and all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)) else \
        -1 if len(vals) > 1 and all(vals[i] < vals[i + 1] for i in range(len(vals) - 1)) else 0
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
    l = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
    rsi = 100 - 100 / (1 + g / l) if l else 100
    m = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = (m - m.ewm(span=9, adjust=False).mean()).iloc[-1]
    return {"ema": ema, "rsi": 1 if rsi > 55 else -1 if rsi < 45 else 0, "macd": sgn(hist)}, rsi


def smc_agent(df):
    h, l, c, n = df.h.values, df.l.values, df.c.values, len(df)
    sh = [i for i in range(2, n - 2) if h[i] == max(h[i - 2:i + 3])]
    sl = [i for i in range(2, n - 2) if l[i] == min(l[i - 2:i + 3])]
    A = h[sh[-1]] if sh else None
    B = l[sl[-1]] if sl else None
    st, sc, traps = 0, 0.0, []
    if len(sh) > 1 and len(sl) > 1:
        hh, hl = h[sh[-1]] > h[sh[-2]], l[sl[-1]] > l[sl[-2]]
        st = 1 if hh and hl else -1 if (not hh and not hl) else 0
    if A and c[-1] > A: sc += 1
    if B and c[-1] < B: sc -= 1
    for i in range(max(0, n - 3), n):
        if A and sh and i > sh[-1] and h[i] > A and c[i] < A:
            sc -= 1.5; traps.append("upside liquidity sweep: breakout buyers can get trapped")
        if B and sl and i > sl[-1] and l[i] < B and c[i] > B:
            sc += 1.5; traps.append("downside liquidity sweep: sellers can get trapped")
    if len(sh) > 1 and abs(h[sh[-1]] - h[sh[-2]]) / h[sh[-1]] < 0.001: traps.append("equal highs: stop-hunt risk above")
    if len(sl) > 1 and abs(l[sl[-1]] - l[sl[-2]]) / l[sl[-1]] < 0.001: traps.append("equal lows: stop-hunt risk below")
    return {"struct": st, "smc": sc}, A, B, traps


def analyse(df, W):
    comp, rsi = indicator_agent(df)
    s, A, B, traps = smc_agent(df)
    comp.update(s)
    score = sum(W.get(k, 1.0) * v for k, v in comp.items())
    if rsi > 75: traps.append("RSI overbought: do not chase")
    return {"b": sgn(score, 1.99), "score": score, "close": float(df.c.iloc[-1]), "A": A, "B": B,
            "traps": traps, "comp": comp}


def devil_agent(direction, R):
    why = []
    for tf, x in R.items():
        if TW[tf] >= 3 and x["b"] == -direction:
            why.append(f"{tf} (higher timeframe) is against this trade")
        for t in x["traps"]:
            why.append(f"{tf}: {t}")
    veto = any("trapped" in w or "higher timeframe" in w for w in why)
    return why, veto


def risk_agent(direction, R):
    tfs = sorted(R, key=lambda t: TW[t])
    lo, entry = R[tfs[0]], R[tfs[0]]["close"]
    if direction > 0 and lo["B"]:
        sl = lo["B"] * 0.999
        tgts = [R[t]["A"] for t in reversed(tfs) if R[t]["A"] and R[t]["A"] > entry]
    elif direction < 0 and lo["A"]:
        sl = lo["A"] * 1.001
        tgts = [R[t]["B"] for t in reversed(tfs) if R[t]["B"] and R[t]["B"] < entry]
    else:
        return None
    if not tgts or abs(entry - sl) == 0 or (direction > 0 and sl >= entry) or (direction < 0 and sl <= entry):
        return None
    tgt = tgts[0]
    rr = abs(tgt - entry) / abs(entry - sl)
    qty = int((CAPITAL * RISK_PCT / 100) / abs(entry - sl))
    return {"entry": entry, "sl": sl, "tgt": tgt, "rr": rr, "qty": qty, "ok": rr >= MIN_RR}


def db():
    d = sqlite3.connect("journal.db")
    d.execute("create table if not exists trades(id integer primary key, ts text, sym text, dir int, entry real, sl real, tgt real, rr real, keys text, out text)")
    d.execute("create table if not exists w(k text primary key, v real)")
    d.execute("create table if not exists bt(sym text, tf text, n int, wins int, exp real, ts text)")
    return d


def weights(d):
    return dict(d.execute("select k,v from w").fetchall())


def learn(d, tid, win):
    row = d.execute("select dir,keys from trades where id=?", (tid,)).fetchone()
    if not row: sys.exit("no such trade")
    W = weights(d)
    for k in row[1].split(","):
        W[k] = max(0.3, min(2.0, W.get(k, 1.0) + (0.1 if win else -0.1)))
        d.execute("insert or replace into w values(?,?)", (k, W[k]))
    d.execute("update trades set out=? where id=?", ("W" if win else "L", tid)); d.commit()
    wins = d.execute("select count(*) from trades where out='W'").fetchone()[0]
    tot = d.execute("select count(*) from trades where out is not null").fetchone()[0]
    print(f"Learned. Closed trades: {tot}, real win rate: {wins / tot * 100:.0f}%")


def say(t):
    print(t)
    try:
        import pyttsx3; e = pyttsx3.init(); e.say(t); e.runAndWait()
    except Exception:
        pass


alerted = {}


def orchestrate(name, data, d):
    ex, tok = WATCH[name]                       # THINK: what to look at
    W, dfs, R = weights(d), {}, {}
    for tf in TFS:                              # ACT: data agent
        dfs[tf] = data.candles(ex, tok, tf)
    if len(dfs["1d"]) > 30: dfs["1w"] = data.weekly(dfs["1d"])
    for tf, df in dfs.items():                  # OBSERVE: indicator + SMC agents
        if len(df) >= 20: R[tf] = analyse(df, W)
    if len(R) < 2: print(name, "not enough data"); return
    tw = sum(TW[t] for t in R)
    agree = sum(TW[t] * x["b"] for t, x in R.items()) / tw
    direction = 1 if agree >= 0.6 else -1 if agree <= -0.6 else 0
    if direction == 0 or abs(agree) < 0.8:
        print(f"{name}: no clear setup (agreement {agree:+.2f}), skip"); return
    why, veto = devil_agent(direction, R)       # Devil's advocate
    r = risk_agent(direction, R)                # Risk agent: 1:3 gate
    if veto or not r or not r["ok"]:
        print(f"{name}: rejected. rr={'n/a' if not r else round(r['rr'], 2)} veto={veto}")
        for w in why: print("   -", w)
        return
    if time.time() - alerted.get((name, direction), 0) < 3600: return
    alerted[(name, direction)] = time.time()    # REPORT
    keys = ",".join(sorted({k for x in R.values() for k, v in x["comp"].items() if v * direction > 0}))
    cur = d.execute("insert into trades(ts,sym,dir,entry,sl,tgt,rr,keys) values(datetime('now'),?,?,?,?,?,?,?)",
                    (name, direction, r["entry"], r["sl"], r["tgt"], r["rr"], keys)); d.commit()
    side = "LONG" if direction > 0 else "SHORT"
    say(f"A plus {side} setup on {name}. Entry {r['entry']:.2f}, stop loss {r['sl']:.2f}, target {r['tgt']:.2f}, "
        f"reward to risk {r['rr']:.1f}. Quantity {r['qty']}. Trade id {cur.lastrowid}.")
    for w in why: print("   caution:", w)


COACH = ("You are the user's trading coach and devil's advocate, speaking simple Hindi (Hinglish ok). "
         "Be honest and direct. Challenge the user's trade ideas, point out mistakes (revenge trading, overtrading, "
         "R:R below 1:3, trading against the higher timeframe, chasing into liquidity sweeps) using the JOURNAL below. "
         "Never claim guaranteed profit or a fixed accuracy. Never give or pretend to have insider information. "
         "You do not place orders. Always remind to use a stop loss and risk 1-2% per trade.")


def popup(title, msg):
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk(); r.withdraw(); r.attributes("-topmost", True)
        messagebox.showwarning(title, msg); r.destroy()
    except Exception:
        pass


def gather(name, data, d):
    ex, tok = WATCH[name]
    W = weights(d)
    dfs = {tf: data.candles(ex, tok, tf) for tf in TFS}
    if len(dfs["1d"]) > 30: dfs["1w"] = data.weekly(dfs["1d"])
    return {tf: analyse(df, W) for tf, df in dfs.items() if len(df) >= 20}


def run_backtest(name, tf, data, d):
    ex, tok = WATCH[name]
    df = data.candles(ex, tok, tf, days=100 if tf != "1d" else 1500).tail(1200).reset_index(drop=True)
    W, res, i = weights(d), [], 60
    while i < len(df) - 2:
        x = analyse(df.iloc[:i + 1].reset_index(drop=True), W)
        dr, e = x["b"], x["close"]
        sl = (x["B"] * 0.999 if x["B"] else None) if dr > 0 else (x["A"] * 1.001 if x["A"] else None)
        if dr == 0 or x["traps"] or sl is None or (dr > 0 and sl >= e) or (dr < 0 and sl <= e):
            i += 1; continue
        tgt = e + dr * MIN_RR * abs(e - sl)
        out, j = None, i + 1
        for j in range(i + 1, len(df)):
            hi, lo = df.h.iloc[j], df.l.iloc[j]
            if (lo <= sl) if dr > 0 else (hi >= sl): out = -1.0; break   # SL checked first = conservative
            if (hi >= tgt) if dr > 0 else (lo <= tgt): out = MIN_RR; break
        if out is None: break
        res.append(out); i = j + 1
    n = len(res); wins = sum(1 for r in res if r > 0)
    exp = sum(res) / n if n else 0.0
    d.execute("insert into bt values(?,?,?,?,?,datetime('now'))", (name, tf, n, wins, exp)); d.commit()
    print(f"Backtest {name} {tf}: {n} trades, win {wins / n * 100 if n else 0:.0f}%, avg result {exp:+.2f}R. "
          "No brokerage/slippage included; the past does not guarantee the future.")


def memory_summary(d):
    tot, wins = d.execute("select count(*), sum(out='W') from trades where out is not null").fetchone()
    lines = [f"Closed trades: {tot}, wins: {wins or 0}"]
    for r in d.execute("select id,ts,sym,dir,entry,sl,tgt,rr,out from trades order by id desc limit 15"):
        lines.append(f"#{r[0]} {r[1]} {r[2]} {'LONG' if r[3] > 0 else 'SHORT'} e={r[4]:.1f} sl={r[5]:.1f} t={r[6]:.1f} rr={r[7]:.1f} result={r[8]}")
    for r in d.execute("select sym,tf,n,wins,exp from bt order by ts desc limit 6"):
        lines.append(f"Backtest {r[0]} {r[1]}: n={r[2]} wins={r[3]} avg={r[4]:+.2f}R")
    lines.append("Signal weights: " + str(weights(d)))
    return "\n".join(lines)


def check(d, data, a):
    sym, side = a[0].upper(), a[1].lower()
    entry, sl, tgt = map(float, a[2:5])
    dr = 1 if side in ("long", "buy", "l") else -1
    block, warn = [], []
    risk = abs(entry - sl)
    rr = abs(tgt - entry) / risk if risk else 0
    if (dr > 0 and not (sl < entry < tgt)) or (dr < 0 and not (tgt < entry < sl)):
        block.append("SL ya target galat taraf hai")
    elif rr < MIN_RR:
        block.append(f"R:R sirf 1:{rr:.1f}; kam se kam 1:{MIN_RR:g} chahiye")
    R = gather(sym, data, d)
    for tf, x in R.items():
        if TW[tf] >= 3 and x["b"] == -dr: block.append(f"{tf} (bada timeframe) tumhare khilaf hai")
        for t in x["traps"]: warn.append(f"{tf}: {t}")
    q = lambda s, *p: d.execute(s, p).fetchone()[0]
    if q("select count(*) from trades where out='L' and ts>datetime('now','-90 minutes')"):
        warn.append("pichhle 90 minute me haar mili: revenge trade ka khatra")
    if q("select count(*) from trades where date(ts)=date('now')") >= 2:
        warn.append("aaj 2+ trade ho chuke: overtrading mat karo")
    last3 = [r[0] for r in d.execute("select out from trades where out is not null order by id desc limit 3")]
    if len(last3) == 3 and all(x == "L" for x in last3):
        block.append("lagatar 3 haar: aaj ruk jao, kal fresh dimaag se dekho")
    outs = [r[0] for r in d.execute("select out from trades where sym=? and dir=? and out is not null", (sym, dr))]
    if len(outs) >= 3 and outs.count("W") / len(outs) < 0.35:
        warn.append(f"{sym} {side} me tumhara record kharab: {outs.count('W')}/{len(outs)} jeet")
    ll = d.execute("select entry,ts from trades where sym=? and dir=? and out='L' order by id desc limit 1", (sym, dr)).fetchone()
    if ll: warn.append(f"pichhli baar {sym} {side} {ll[0]:.1f} par ({ll[1]}) me fase the")
    b = d.execute("select n,wins,exp from bt where sym=? order by ts desc limit 1", (sym,)).fetchone()
    if b and b[2] < 0: warn.append(f"backtest me is system ka average {b[2]:+.2f}R (nuksan)")
    if block:
        msg = "ROKO, trade mat lo:\n- " + "\n- ".join(block + warn)
        popup("TRADE ROKO", msg); say(msg); return
    msg = ("Savdhan:\n- " + "\n- ".join(warn)) if warn else "Koi galti nahi mili. SL zaroor lagao."
    say(msg)
    if warn: popup("Savdhan", msg)
    if input("Trade liya? (y/n): ").lower().startswith("y"):
        lo = R[min(R, key=lambda t: TW[t])]
        keys = ",".join(sorted(k for k, v in lo["comp"].items() if v * dr > 0))
        d.execute("insert into trades(ts,sym,dir,entry,sl,tgt,rr,keys) values(datetime('now'),?,?,?,?,?,?,?)",
                  (sym, dr, entry, sl, tgt, rr, keys)); d.commit()
        print("Journal me save. Result ke baad: python trade_agents.py result <id> win|loss")


def chat(d):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY .env me daalo. Tab tak yeh summary dekho:\n" + memory_summary(d)); return
    import anthropic
    cl, hist = anthropic.Anthropic(api_key=key), []
    print("Coach taiyar hai. 'exit' likhkar bahar nikalo.")
    while True:
        q = input("Aap: ").strip()
        if q.lower() in ("exit", "quit"): break
        hist.append({"role": "user", "content": q})
        r = cl.messages.create(model=os.getenv("COACH_MODEL", "claude-sonnet-5-5"), max_tokens=700,
                               system=COACH + "\n\nJOURNAL:\n" + memory_summary(d), messages=hist)
        t = r.content[0].text
        hist.append({"role": "assistant", "content": t}); say(t)


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "once"
    d = db()
    if cmd == "result": return learn(d, int(a[1]), a[2] == "win")
    if cmd == "chat": return chat(d)
    if cmd in ("fii", "sectors", "news"):
        import fii_news
        if cmd == "fii": fii_news.fii_dii(); fii_news.large_deals()
        elif cmd == "sectors": fii_news.sectors(a[1])
        else: fii_news.news()
        return
    data = DataAgent()
    if cmd == "csv":
        print(data.candles(*WATCH[a[1]], a[2])[["o", "h", "l", "c"]].to_csv(index=False, header=False)); return
    if cmd == "check": return check(d, data, a[1:])
    if cmd == "backtest": return run_backtest(a[1].upper(), a[2] if len(a) > 2 else "15m", data, d)
    names = a[1:] or list(WATCH)
    while True:
        for n in names: orchestrate(n, data, d)
        if cmd != "scan": break
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
