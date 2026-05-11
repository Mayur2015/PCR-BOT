import requests
import time
import json
import csv
import os
from datetime import datetime
import pytz
from SmartApi import SmartConnect
import pyotp

# ===== TELEGRAM CONFIG =====
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== GLOBAL VARIABLES =====
smartApi = None
last_heartbeat_hour = -1

# ===== TELEGRAM SEND =====
def send_telegram(msg):

    try:

        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

        payload = {
            "chat_id": CHAT_ID,
            "text": msg
        }

        response = requests.post(url, data=payload, timeout=10)

        print("TELEGRAM STATUS:", response.status_code)
        print("TELEGRAM RESPONSE:", response.text)

    except Exception as e:

        print("Telegram Error:", e)

# ===== ERROR LOGGER =====
def log_error(error_msg):

    with open("error_log.txt", "a") as f:

        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)

        f.write(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] {error_msg}\n")

# ===== LOGIN FUNCTION =====
def login():

    global smartApi

    try:

        api_key = os.getenv("API_KEY")
        client_id = os.getenv("CLIENT_ID")
        password = os.getenv("PASSWORD")
        totp_key = os.getenv("TOTP_KEY")

        smartApi = SmartConnect(api_key)

        totp = pyotp.TOTP(totp_key).now()

        data = smartApi.generateSession(client_id, password, totp)

        if data['status']:

            print("LOGIN SUCCESS")

            send_telegram("🌅 PCR SYSTEM STARTED SUCCESSFULLY")

            return True

        else:

            print("LOGIN FAILED")

            send_telegram("❌ LOGIN FAILED")

            return False

    except Exception as e:

        print("LOGIN ERROR:", e)

        send_telegram(f"❌ LOGIN ERROR\n{e}")

        return False

# ===== SAFE API CALL =====
def safe_ltp(exchange, symbol, token, retry=3):

    global smartApi

    for _ in range(retry):

        try:

            data = smartApi.ltpData(exchange, symbol, token)

            # SUCCESS
            if data and data.get("data"):

                return data["data"]["ltp"]

            # INVALID TOKEN
            if data.get("message") == "Invalid Token":

                print("SESSION EXPIRED - RELOGIN")

                send_telegram("♻️ SESSION EXPIRED - RELOGIN")

                login()

                time.sleep(3)

        except Exception as e:

            print("LTP Retry Error:", e)

            log_error(str(e))

            send_telegram(f"❌ LTP ERROR\n{e}")

            login()

            time.sleep(3)

    return None

# ===== LOGIN START =====
if not login():
    exit()

# ===== SYMBOL MASTER =====
url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

symbols = requests.get(
    url,
    headers={"Cache-Control": "no-cache"}
).json()

# ===== MAIN LOOP =====
while True:

    try:

        ist = pytz.timezone("Asia/Kolkata")

        now = datetime.now(ist)

        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # ===== HEARTBEAT =====
        if now.minute == 0 and now.second < 5 and now.hour % 2 == 0:

            if last_heartbeat_hour != now.hour:

                send_telegram(f"✅ BOT RUNNING HEALTHY\nTime: {time_str}")

                last_heartbeat_hour = now.hour

        # ===== MODE =====
        if now.weekday() >= 5:

            mode = "WEEKEND"

        elif now.hour < 9 or now.hour > 15:

            mode = "AFTER MARKET"

        else:

            mode = "LIVE MARKET"

        # ===== NIFTY =====
        nifty = safe_ltp("NSE", "NIFTY", "26000")

        if nifty is None:

            print("NIFTY FETCH FAILED")

            time.sleep(5)

            continue

        # ===== OPTION FILTER =====
        opts = [

            s for s in symbols

            if s['exch_seg'] == "NFO"
            and "NIFTY" in s.get('symbol', '')
            and (
                "CE" in s.get('symbol', '')
                or "PE" in s.get('symbol', '')
            )
        ]

        expiries = list(set([o['expiry'] for o in opts]))

        exp_list = []

        today = now.date()

        for e in expiries:

            try:

                d = datetime.strptime(e, "%d%b%Y").date()

                if d >= today:

                    exp_list.append((e, d))

            except:

                pass

        exp_list = sorted(exp_list, key=lambda x: x[1])

        expiry = exp_list[0][0]

        filtered = [

            o for o in opts

            if o['expiry'] == expiry
        ]

        atm = round(nifty / 50) * 50

        strike_map = {

            o['token']: int(float(o['strike']) / 100)

            for o in filtered
        }

        tokens = [

            o['token']

            for o in filtered

            if atm - 500 <= strike_map[o['token']] <= atm + 500
        ]

        fetched = []

        for i in range(0, len(tokens), 50):

            data = smartApi.getMarketData(

                "FULL",

                {"NFO": tokens[i:i+50]}
            )

            if data['status']:

                fetched += data['data']['fetched']

        total_ce = 0
        total_pe = 0

        atm_ce = 0
        atm_pe = 0

        strike_data = {}

        for item in fetched:

            sym = item['tradingSymbol']

            oi = item['opnInterest']

            tk = item['symbolToken']

            strike = strike_map.get(tk)

            if strike is None:

                continue

            if strike not in strike_data:

                strike_data[strike] = {
                    "ce": 0,
                    "pe": 0
                }

            if sym.endswith("CE"):

                total_ce += oi

                strike_data[strike]["ce"] += oi

                if strike == atm:

                    atm_ce = oi

            else:

                total_pe += oi

                strike_data[strike]["pe"] += oi

                if strike == atm:

                    atm_pe = oi

        # ===== PCR =====
        pcr = total_pe / total_ce if total_ce else 0

        atm_pcr = atm_pe / atm_ce if atm_ce else 0

        # ===== MAX PAIN =====
        max_pain = min(

            strike_data,

            key=lambda x: sum(

                (x-k)*v["ce"]

                if x > k

                else (k-x)*v["pe"]

                for k, v in strike_data.items()
            )
        )

        # ===== OUTPUT =====
        print("\n============================")
        print("Time:", time_str)
        print("Mode:", mode)
        print("============================")
        print("NIFTY:", round(nifty, 2))
        print("PCR:", round(pcr, 4),
              "| ATM PCR:", round(atm_pcr, 4))
        print("Max Pain:", max_pain)

    except Exception as e:

        print("MAIN LOOP ERROR:", e)

        log_error(str(e))

        send_telegram(f"❌ MAIN LOOP ERROR\n{e}")

        login()

        time.sleep(5)

    time.sleep(180)
