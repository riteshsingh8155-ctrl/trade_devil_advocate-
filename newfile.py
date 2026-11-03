import requests, statistics, time
from datetime import datetime

SYMBOL = "PAXG-USD"
print("GOLD v37 OPERATOR FINAL STARTING...")
print("ADX Bug Fixed + Threshold Auto\n")

def get_data():
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}?interval=1m&range=1d"
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15).json()
        result = r['chart']['result'][0]
        q = result['indicators']['quote'][0]
        closes = [x for x in q['close'] if x is not None]
        highs = [x for x in q['high'] if x is not None]
        lows = [x for x in q['low'] if x is not None]
        opens = [x for x in q['open'] if x is not None]
        return closes, opens, highs, lows
    except Exception as e:
        print(f"Error {e}")
        return None

def calc_atr(h,l,c,p=14):
    tr = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1,len(c))]
    atr = sum(tr[:p])/p
    for x in tr[p:]:
        atr = (atr*(p-1)+x)/p
    return atr

def calc_adx_advanced(h,l,c,p=14):
    # FIXED ADX - 1m me ADX 4 isliye aa raha tha kyuki period kam tha
    # Ab 14 period ka proper Wilder ADX
    if len(c) < 50:
        return 15, 20, 20
    pdm, mdm, tr = [],[],[]
    for i in range(1,len(c)):
        up = h[i]-h[i-1]
        dn = l[i-1]-l[i]
        pdm.append(up if up>dn and up>0 else 0)
        mdm.append(dn if dn>up and dn>0 else 0)
        tr.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    tr14 = sum(tr[:p])
    p14 = sum(pdm[:p])
    m14 = sum(mdm[:p])
    dx_list = []
    for i in range(p, len(tr)):
        tr14 = tr14 - tr14/p + tr[i]
        p14 = p14 - p14/p + pdm[i]
        m14 = m14 - m14/p + mdm[i]
        plus = 100*p14/tr14 if tr14!=0 else 0
        minus = 100*m14/tr14 if tr14!=0 else 0
        dx = 100*abs(plus-minus)/(plus+minus) if plus+minus!=0 else 0
        dx_list.append(dx)
        if i == len(tr)-1:
            final_plus = plus
            final_minus = minus
    # ADX = Average of last 14 DX
    adx = sum(dx_list[-14:])/14 if len(dx_list)>=14 else sum(dx_list)/len(dx_list) if dx_list else 15
    return adx, final_plus, final_minus

ai_th = 12  # FIX: Pehle 22 tha, ab 12 kiya kyuki 1m me ADX 4-15 hi rehta hai

while True:
    try:
        data = get_data()
        if not data:
            time.sleep(3)
            continue
        closes, opens, highs, lows = data
        c = closes[-1]
        
        atr = calc_atr(highs[-25:], lows[-25:], closes[-25:])
        adx, plus_di, minus_di = calc_adx_advanced(highs, lows, closes)
        
        body = abs(closes[-2]-opens[-2])
        avg_body = statistics.mean([abs(closes[i]-opens[i]) for i in range(-15,-2)])
        body_ratio = body / avg_body if avg_body>0 else 1
        
        # OPERATOR LOGIC
        zh = max(highs[-15:])
        zl = min(lows[-15:])
        zw = zh - zl
        avg_lw = statistics.mean([closes[i]-lows[i] for i in range(-12,0)])
        avg_uw = statistics.mean([highs[i]-closes[i] for i in range(-12,0)])
        
        now = datetime.now().strftime("%H:%M:%S")
        
        # OPERATOR SYMBOL
        if zw < 5.0:
            if avg_lw > avg_uw*1.3:
                print(f"[{now}] 🦈 OPERATOR BUY MAAL {zl:.0f}-{zh:.0f} pe BHAR RAHA HAI 🟢 ZoneW {zw:.1f}$ LW {avg_lw:.1f} UW {avg_uw:.1f}")
            elif avg_uw > avg_lw*1.3:
                print(f"[{now}] 🦈 OPERATOR SELL MAAL {zl:.0f}-{zh:.0f} pe BHAR RAHA HAI 🔴 ZoneW {zw:.1f}$")
            else:
                print(f"[{now}] 🦈 OPERATOR MAAL BHAR RAHA HAI {zl:.0f}-{zh:.0f} pe | ZoneW {zw:.1f}$ ATR {atr:.1f}$ - BLAST READY")
        
        # CALL PUT - FIXED LOGIC - ADX low pe bhi chalega
        # Ab ADX 12 se upar ho to hi, aur Body 1.2x se zyada ho to signal
        adx_ok = adx > ai_th
        body_ok = body_ratio > 1.2  # Pehle 1.5 tha, ab 1.2 kiya
        
        if adx_ok and body_ok and atr > 0.7:
            if plus_di > minus_di + 3 and closes[-2] > opens[-2]:
                print(f"\a[{now}] 🚀🚀 CALL BUY | GOLD {c:.2f} ADX {adx:.0f} DI+ {plus_di:.0f}>DI- {minus_di:.0f} Body {body_ratio:.1f}x | 📳 CALL 📈📈")
            elif minus_di > plus_di + 3 and closes[-2] < opens[-2]:
                print(f"\a[{now}] 🔻🔻 PUT BUY | GOLD {c:.2f} ADX {adx:.0f} DI- {minus_di:.0f}>DI+ {plus_di:.0f} Body {body_ratio:.1f}x | 📳 PUT 📉📉")
            else:
                print(f"[{now}] 🔥 READY | GOLD {c:.2f} ADX {adx:.0f} ATR {atr:.1f} Body {body_ratio:.1f}x DI+ {plus_di:.0f} DI- {minus_di:.0f}")
        else:
            print(f"[{now}] 🤖 AI SCAN | GOLD {c:.2f} ADX {adx:.0f}/{ai_th} ATR {atr:.1f} Body {body_ratio:.1f}x ZoneW {zw:.1f}$")
        
        # AI auto learn
        if adx < ai_th:
            ai_th = max(9, ai_th - 0.1)
        
        time.sleep(8)
        
    except Exception as e:
        print(f"Loop Error {e}")
        time.sleep(3)