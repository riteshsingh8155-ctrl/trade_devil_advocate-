from SmartApi import SmartConnect
import pyotp
import pandas as pd
import time
from datetime import datetime, timedelta
from secrets import API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET

obj = SmartConnect(api_key=API_KEY)

def login():
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = obj.generateSession(CLIENT_CODE, PASSWORD, totp)
    print("Login OK:", data['data']['userName'])

def get_candle(token, interval):
    try:
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": interval,
            "fromdate": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"),
            "todate": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        hist = obj.getCandleData(params)
        df = pd.DataFrame(hist['data'], columns=['time','open','high','low','close','volume'])
        df['ema_50'] = df['close'].ewm(span=50).mean()
        df['ema_200'] = df['close'].ewm(span=200).mean()
        df['vol_avg'] = df['volume'].rolling(20).mean()
        return df
    except Exception as e:
        print("Error:", e)
        return None

def check_market():
    df_15 = get_candle("26000", "FIFTEEN_MINUTE")
    if df_15 is None:
        print("Data ka wait...")
        return
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Nifty: {df_15['close'].iloc[-1]} | EMA50: {df_15['ema_50'].iloc[-1]:.2f}")

login()
check_market()