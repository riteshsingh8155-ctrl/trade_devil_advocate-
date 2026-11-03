"""FII/DII flows, sector ranking and news scanner: PUBLIC data only (NSE, NSDL, RSS headlines).
Sources can change their URLs/format; if one breaks, the function prints the error and the rest still work."""
import datetime as dt

HDR = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Referer": "https://www.nseindia.com/"}
FEEDS = ["https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
         "https://www.moneycontrol.com/rss/marketreports.xml",
         "https://www.livemint.com/rss/markets"]
POS = ["surge", "rally", "jump", "gain", "record high", "upgrade", "beat", "profit rises", "strong", "buy", "order win", "bullish", "inflow"]
NEG = ["fall", "plunge", "slump", "crash", "downgrade", "miss", "loss", "weak", "sell-off", "selloff", "probe", "fraud", "bearish", "outflow", "ban"]
TAGS = {"NIFTY": ["nifty", "sensex", "dalal street"], "BANKS": ["bank", "hdfc", "icici", "sbi", "axis", "rbi"],
        "IT": ["infosys", "tcs", "wipro", "hcl", "it stocks"], "CRUDE": ["crude", "brent", "opec", "oil"],
        "GOLD": ["gold", "bullion"], "SILVER": ["silver"], "AUTO": ["auto", "maruti", "tata motors", "mahindra"],
        "PHARMA": ["pharma", "sun pharma", "cipla", "drug"], "METALS": ["steel", "metal", "hindalco", "vedanta"],
        "FII": ["fii", "fpi", "foreign investors", "foreign portfolio"]}


def sentiment(text):
    t = text.lower()
    return sum(w in t for w in POS) - sum(w in t for w in NEG)


def tags(text):
    t = text.lower()
    return [k for k, ws in TAGS.items() if any(w in t for w in ws)]


def _nse():
    import requests
    s = requests.Session(); s.headers.update(HDR)
    s.get("https://www.nseindia.com", timeout=15)
    return s


def fii_dii():
    try:
        for x in _nse().get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=15).json():
            print(f"{x.get('category')}: {x.get('date')} buy {x.get('buyValue')} sell {x.get('sellValue')} NET {x.get('netValue')} Cr")
    except Exception as e:
        print("FII/DII cash fetch failed:", e)
    try:
        import pandas as pd
        for back in range(0, 6):
            d = dt.date.today() - dt.timedelta(days=back)
            url = f"https://archives.nseindia.com/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"
            r = _nse().get(url, timeout=15)
            if r.status_code == 200 and "Client" in r.text:
                from io import StringIO
                df = pd.read_csv(StringIO(r.text), skiprows=1); df.columns = [c.strip() for c in df.columns]
                f = df[df.iloc[:, 0].str.strip() == "FII"].iloc[0]
                L, S = float(f["Future Index Long"]), float(f["Future Index Short"])
                print(f"FII index futures ({d}): long {L:.0f}, short {S:.0f}, long% {L / (L + S) * 100:.0f}%: "
                      + ("net long (bullish tilt)" if L > S else "net short (bearish tilt)"))
                break
    except Exception as e:
        print("FII index-futures fetch failed:", e)


def large_deals():
    try:
        j = _nse().get("https://www.nseindia.com/api/snapshot-capital-market-largedeal", timeout=15).json()
        rows = []
        for k in ("BULK_DEALS_DATA", "BLOCK_DEALS_DATA"):
            for x in j.get(k, []):
                nm = str(x.get("clientName", "")).upper()
                if x.get("buySell") == "BUY" and any(w in nm for w in ("FPI", "FII", "MAURITIUS", "SINGAPORE", "FOREIGN", "INVESTMENT FUND", "LUXEMBOURG")):
                    rows.append((x.get("symbol"), nm[:40], float(x.get("qty", 0)) * float(x.get("watp", 0)) / 1e7))
        rows.sort(key=lambda r: -r[2])
        print("Foreign-looking BUYS in bulk/block deals today (classified by client NAME, so approximate):")
        for s, n, v in rows[:10]: print(f"  {s}: {v:.1f} Cr  ({n})")
        if not rows: print("  none found")
    except Exception as e:
        print("Large-deals fetch failed:", e)
    print("Note: daily stock-wise FII buying is NOT published. Only deals above and quarterly shareholding patterns are public.")


def sectors(path):
    import pandas as pd
    df = pd.read_csv(path)  # columns: sector,net  (from NSDL FPI sector-wise report, Rs Cr)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.sort_values("net", ascending=False)
    print("FPI sector flows (most buying first):")
    for _, r in df.iterrows(): print(f"  {r['sector']}: {r['net']:+.0f} Cr")


def news():
    import feedparser
    score, seen = {}, set()
    for u in FEEDS:
        try:
            for e in feedparser.parse(u).entries[:25]:
                h = e.get("title", "")
                if h in seen: continue
                seen.add(h); s = sentiment(h + " " + e.get("summary", "")); tg = tags(h)
                for t in tg: score[t] = score.get(t, 0) + s
                if tg or s: print(f"[{'+' if s > 0 else '-' if s < 0 else '0'}] {h}  {tg}")
        except Exception as ex:
            print("feed failed:", u, ex)
    print("\nNet public-news tilt by tag (keyword-based, rough):")
    for t, v in sorted(score.items(), key=lambda x: -x[1]): print(f"  {t}: {v:+d}")
