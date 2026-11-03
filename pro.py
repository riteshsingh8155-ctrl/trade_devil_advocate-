import requests
from smartapi import SmartConnect
import pyotp

API_KEY = "dOgfiXS0"
CLIENT_CODE = "R1001550"
PASSWORD = "2002"
TELEGRAM_BOT_TOKEN = "8534769215:AAGSTXW_0gztZk9qcSoTCiQa819YWoiXVX8"
TELEGRAM_CHAT_ID = "8872099638"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

try:
    send_telegram("🤖 Bot Start ho raha hai...")
    totp_input = input("Angel App ka 6 digit TOTP code daal: ")
    
    smartApi = SmartConnect(api_key=API_KEY)
    data = smartApi.generateSession(CLIENT_CODE, PASSWORD, totp_input)

    if data['status']:
        print("Login Success")
        send_telegram("✅ Login Success ho gaya bhai!")
    else:
        print("Login Fail:", data['message'])
        send_telegram("❌ Login Fail: " + data['message'])

except Exception as e:
    print("Error:", e)
    send_telegram("⚠️ Error: " + str(e))