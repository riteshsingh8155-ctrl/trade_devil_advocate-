"""
TradeBot: technical signal (EMA + MACD + RSI) + Option Buyer criteria (Delta, IV, Theta, Gamma)

Install:  pip install pandas numpy requests
Run:      python trade_bot.py

Telegram alert (optional):
    export TG_TOKEN="naya_token"
    export TG_CHAT_ID="your_chat_id"

NOTE: Sirf educational hai, financial advice nahi. Pehle paper-trade karo.
"""
import os
import math
import time
import numpy as np
import pandas as pd
import requests

# ---------------- CONFIG ----------------
CONFIG = {
    "symbol": "BTCUSDT",
    "interval": "1h",
    "limit": 300,            # EMA_200 ke liye 200+ candles chahiye

    # Option buyer criteria (reel wale rules) - apne market ke hisaab se badlo
    "min_delta": 0.30,       # delta 0.30 se neeche ho to buy mat karo
    "max_iv": 15.0,          # IV % - 15 se neeche ho tabhi buy (NIFTY jaise index ke liye)
    "max_theta_pct": 5.0,    # roz ka theta decay premium ka max 5%
    "min_gamma": 0.0012,     # gamma 0.0012 se upar

    # Option inputs (live option chain nahi hai, isliye manual)
    "iv_input": 14.0,        # current IV % (broker/option chain se dekh kar daalo)
    "days_to_expiry": 7,
    "risk_free_rate": 0.07,
    "strike_step": 100,      # strike gap (NIFTY=50, BTC=500/1000 etc.)
    "strikes_each_side": 5,
}
# ----------------------------------------


# ---------- Indicators (pandas only, talib ki zaroorat nahi) ----------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def macd(s, fast=12, slow=26, sig=9):
    line = ema(s, fast) - ema(s, slow)
    signal = ema(line, sig)
    return line, signal, line - signal


# ---------- Black-Scholes Greeks ----------
def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def _pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def bs_greeks(spot, strike, days, iv_pct, r, kind="CALL"):
    T = max(days, 0.01) / 365.0
    sigma = iv_pct / 100.0
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    gamma = _pdf(d1) / (spot * sigma * math.sqrt(T))
    if kind == "CALL":
        price = spot * _cdf(d1) - strike * math.exp(-r * T) * _cdf(d2)
        delta = _cdf(d1)
        theta = (-spot * _pdf(d1) * sigma / (2 * math.sqrt(T))
                 - r * strike * math.exp(-r * T) * _cdf(d2)) / 365
    else:
        price = strike * math.exp(-r * T) * _cdf(-d2) - spot * _cdf(-d1)
        delta = _cdf(d1) - 1
        theta = (-spot * _pdf(d1) * sigma / (2 * math.sqrt(T))
                 + r * strike * math.exp(-r * T) * _cdf(-d2)) / 365
    return {"price": price, "delta": delta, "gamma": gamma, "theta": theta}


class TradeBot:
    def __init__(self, symbol, cfg=CONFIG):
        self.symbol = symbol
        self.cfg = cfg
        self.data = None

    # ---------- Data ----------
    def fetch_data(self):
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": self.symbol, "interval": self.cfg["interval"], "limit": self.cfg["limit"]}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        df = pd.DataFrame(r.json(), columns=[
            "Open Time", "Open", "High", "Low", "Close", "Volume", "Close Time",
            "Quote Asset Volume", "Number of Trades",
            "Taker Buy Base Asset Volume", "Taker Buy Quote Asset Volume", "Ignore"])
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            df[c] = df[c].astype(float)
        self.data = df

    def calculate_indicators(self):
        c = self.data["Close"]
        for n in (9, 21, 50, 200):
            self.data[f"EMA_{n}"] = ema(c, n)
        self.data["MACD"], self.data["MACD_Signal"], self.data["MACD_Hist"] = macd(c)
        self.data["RSI"] = rsi(c)

    def analyze(self):
        self.calculate_indicators()
        return self.data.iloc[-1]

    # ---------- Direction signal ----------
    def check_entry_exit(self):
        d = self.analyze()
        bullish = d["EMA_9"] > d["EMA_21"] > d["EMA_50"] and d["MACD_Hist"] > 0 and 40 < d["RSI"] < 70
        bearish = d["EMA_9"] < d["EMA_21"] < d["EMA_50"] and d["MACD_Hist"] < 0 and 30 < d["RSI"] < 60
        oversold_bounce = d["RSI"] < 30 and d["MACD_Hist"] > 0
        overbought_drop = d["RSI"] > 70 and d["MACD_Hist"] < 0
        if bullish or oversold_bounce:
            return "Buy Signal"
        if bearish or overbought_drop:
            return "Sell Signal"
        return "No Action"

    # ---------- Option buyer filter ----------
    def option_buyer_scan(self, signal):
        """Buy Signal -> CALL scan, Sell Signal -> PUT scan"""
        cfg = self.cfg
        if signal == "No Action":
            return []
        kind = "CALL" if signal == "Buy Signal" else "PUT"
        spot = float(self.data["Close"].iloc[-1])
        atm = round(spot / cfg["strike_step"]) * cfg["strike_step"]

        # Rule: IV 15 se neeche ho tabhi buy (option sasta hoga)
        if cfg["iv_input"] > cfg["max_iv"]:
            print(f"IV {cfg['iv_input']}% > {cfg['max_iv']}% -> option buy avoid (seller ka zone)")
            return []

        picks = []
        for i in range(-cfg["strikes_each_side"], cfg["strikes_each_side"] + 1):
            k = atm + i * cfg["strike_step"]
            g = bs_greeks(spot, k, cfg["days_to_expiry"], cfg["iv_input"], cfg["risk_free_rate"], kind)
            if g["price"] <= 0:
                continue
            theta_pct = abs(g["theta"]) / g["price"] * 100
            ok = (abs(g["delta"]) >= cfg["min_delta"]      # delta >= 0.30
                  and g["gamma"] >= cfg["min_gamma"]        # gamma >= 0.0012
                  and theta_pct <= cfg["max_theta_pct"])    # theta decay kam
            picks.append({
                "type": kind, "strike": k, "premium": round(g["price"], 2),
                "delta": round(g["delta"], 3), "gamma": round(g["gamma"], 5),
                "theta/day": round(g["theta"], 2), "theta_%": round(theta_pct, 2),
                "PASS": ok})
        return pd.DataFrame(picks)

    # ---------- Alerts ----------
    def send_telegram(self, text):
        token, chat = os.getenv("TG_TOKEN"), os.getenv("TG_CHAT_ID")
        if not token or not chat:
            return
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=10)
        except Exception as e:
            print("Telegram error:", e)

    # ---------- One cycle ----------
    def run_once(self):
        self.fetch_data()
        signal = self.check_entry_exit()
        last = self.data.iloc[-1]
        print(f"\n[{time.strftime('%H:%M:%S')}] {self.symbol} price={last['Close']:.2f} "
              f"RSI={last['RSI']:.1f} MACD_Hist={last['MACD_Hist']:.3f} -> {signal}")

        scan = self.option_buyer_scan(signal)
        msg = f"{self.symbol}: {signal}"
        if isinstance(scan, pd.DataFrame) and not scan.empty:
            passed = scan[scan["PASS"]]
            print(scan.to_string(index=False))
            if passed.empty:
                print("Koi strike Delta/Gamma/Theta criteria pass nahi kar raha -> trade skip")
            else:
                best = passed.iloc[0]
                msg += f"\nBest {best['type']} strike {best['strike']} premium {best['premium']} delta {best['delta']}"
                print("\nPASS strikes:\n", passed.to_string(index=False))
        if signal != "No Action":
            self.send_telegram(msg)

    def run_forever(self, every_sec=300):
        while True:
            try:
                self.run_once()
            except Exception as e:
                print("Error:", e)
            time.sleep(every_sec)


if __name__ == "__main__":
    bot = TradeBot(CONFIG["symbol"])
    bot.run_once()          # ek baar chalane ke liye
    # bot.run_forever(300)  # har 5 min loop mein chalane ke liye (uncomment karo)
