from enum import Enum
import ccxt
import time
from datetime import datetime, timedelta
import requests  # 텔레그램 메시지 전송용
import configparser
import logging
from logging.handlers import TimedRotatingFileHandler
import os

# ==========================================================
# Logger 설정
# ==========================================================
os.makedirs("logs", exist_ok=True)

logger = logging.getLogger("Arbitrage")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    "[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# 콘솔 출력
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# 날짜별 로그 파일 생성
file_handler = TimedRotatingFileHandler(
    filename="logs/arbitrage.log",
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8"
)
file_handler.suffix = "%Y-%m-%d"

# 파일명 변경
file_handler.namer = lambda default_name: (
    f"logs/{os.path.basename(default_name).split('.')[-1]}_arbitrage.log"
)

file_handler.setFormatter(formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# ==========================================================
# 0. Print & Logger 함수 정의
# ==========================================================
def log(message):
    # print(message)
    logger.info(message)

def error(message):
    # print(message)
    logger.error(message)

# ==========================================================
# 1. 이넘(Enum) 클래스 선언
# ==========================================================
class Exchange(Enum):
    BITGET = "bitget"
    GATEIO = "gateio"

# ==========================================================
# 2. 텔레그램 및 봇 설정값 (본인의 정보로 변경 필수)
# ==========================================================
config = configparser.ConfigParser()
config.read('secret_keys.txt', encoding='utf-8')

TELEGRAM_TOKEN = config['TELEGRAM']['TOKEN']  # BotFather에게 받은 토큰
TELEGRAM_CHAT_ID = config['TELEGRAM']['CHAT_ID']  # ID봇 등에게 받은 내 채팅방 ID

SYMBOL = 'NESS/USDT'
DIFFERENT_RATE = 0.5 # 차이 기준 퍼센트 (0.5%)
DELAY_SECOND = 60
MIN_TRADE_USDT = 1.5

start_dt = datetime.now()

# ==========================================================
# 💡 Alert 설정: 1시간 알림 제한을 위한 시간 저장 변수
# ==========================================================
ALERT_COOLDOWN_SECOND = 3600  # 1시간

# 프로그램 시작 직후에는 바로 알림을 보낼 수 있도록 초기값 설정
last_alert_time = start_dt - timedelta(seconds=ALERT_COOLDOWN_SECOND)

# 거래소 객체 생성
bitget = ccxt.bitget({
    'apiKey': config['BITGET']['ACCESS_API_KEY'],
    'secret': config['BITGET']['SECRET_KEY'],
    'password': config['BITGET']['PASSPHRASE']
})
gateio = ccxt.gate({
    'apiKey': config['GATEIO']['ACCESS_API_KEY'],
    'secret': config['GATEIO']['SECRET_KEY']
})

exchanges = {
    Exchange.BITGET: bitget,
    Exchange.GATEIO: gateio
}

start_time = start_dt.strftime('%Y-%m-%d %H:%M:%S')
log(f"[{start_time}] 🚀 NESS Arbitrage Start...")

# ==========================================================
# 3. 텔레그램 전송 함수 정의
# ==========================================================
def send_telegram_message(message):
    """지정한 텔레그램 채팅방으로 메시지를 전송하는 함수"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            error(f"★ [LOG] ❌ [Telegram] 전송 실패 (코드: {response.status_code})")
    except Exception as e:
        error(f"★ [LOG] ❌ [Telegram] 에러 발생: {e}")

# ==========================================================
# 4. 주문 수량 계산
# ==========================================================
def calculate_trade_amount(tradable_usdt, buy_price, sell_price):
    """거래 가능한 USDT를 기준으로 매수/매도 주문 수량 계산"""
    buy_amount = int(tradable_usdt / buy_price)
    sell_amount = int(tradable_usdt / sell_price)

    return buy_amount, sell_amount

# ==========================================================
# 4. 메인 실행 루프
# ==========================================================
while True:
    try:
        now_dt = datetime.now()
        now_str = now_dt.strftime('%Y-%m-%d %H:%M:%S')
        log(f"========== [{now_str}] LOOP START ==========")

        # 매수/매도 담당 거래소를 저장할 이넘 변수 초기화
        buy_exchange = None
        sell_exchange = None

        # 양쪽 거래소 자산 정보 불러오기
        bg_balance = bitget.fetch_balance()
        gate_balance = gateio.fetch_balance()
        log("★ [LOG] Balance fetched successfully")

        balances = {
            Exchange.BITGET: bg_balance,
            Exchange.GATEIO: gate_balance,
        }


        # 두 거래소의 호가창(Orderbook) 조회
        # 비트겟 호가
        bg_orderbook = bitget.fetch_order_book(SYMBOL)
        bg_bid = bg_orderbook['bids'][0][0]  # 내가 팔 수 있는 가장 높은 가격 (매수 1호가)
        bg_bid_vol = bg_orderbook['bids'][0][1] # 해당 호가의 잔량
        bg_ask = bg_orderbook['asks'][0][0]
        bg_ask_vol = bg_orderbook['asks'][0][1]
        
        # 게이트아이오 호가
        gate_orderbook = gateio.fetch_order_book(SYMBOL)
        gate_bid = gate_orderbook['bids'][0][0]
        gate_bid_vol = gate_orderbook['bids'][0][1]
        gate_ask = gate_orderbook['asks'][0][0] # 내가 살 수 있는 가장 낮은 가격 (매도 1호가)
        gate_ask_vol = gate_orderbook['asks'][0][1] # 해당 호가의 잔량

        log(
            f"★ [LOG] Orderbook "
            f"BG Ask:{bg_ask} Bid:{bg_bid} | "
            f"Gate Ask:{gate_ask} Bid:{gate_bid}"
        )

        # -------------------------------------------------------------
        # 오더북 조건 체크
        # -------------------------------------------------------------
        best_case = None
        best_percent = 0

        cases = [
            { # case 1: 비트겟에서 사서 ➡️ 게이트에 파는 경우
                "buy": Exchange.BITGET,
                "sell": Exchange.GATEIO,
                "buy_price": bg_ask,
                "buy_vol": bg_ask_vol,
                "sell_price": gate_bid,
                "sell_vol": gate_bid_vol,
                "label": "BITGET -> GATEIO"
            },
            { # case 2: 게이트에서 사서 ➡️ 비트겟에 파는 경우
                "buy": Exchange.GATEIO,
                "sell": Exchange.BITGET,
                "buy_price": gate_ask,
                "buy_vol": gate_ask_vol,
                "sell_price": bg_bid,
                "sell_vol": bg_bid_vol,
                "label": "GATEIO -> BITGET"
            }
        ]

        for case in cases:
            percent = ((case["sell_price"] - case["buy_price"]) / case["buy_price"]) * 100
            log(
                f"★ [LOG] {case['label']} "
                f"{percent:.3f}%"
            )

            if percent >= DIFFERENT_RATE and percent > best_percent:
                best_percent = percent
                best_case = case

        if best_case is None:
            log("★ [LOG] No arbitrage opportunity")
            continue

        buy_exchange = best_case["buy"]
        sell_exchange = best_case["sell"]

        log_msg = (
            f"[{best_case['label']}] 차이: {best_percent:.2f}% "
            f"매수: {buy_exchange.value.upper()} "
            f"(가격: {best_case['buy_price']} / 수량: {best_case['buy_vol']:.2f}) "
            f"매도: {sell_exchange.value.upper()} "
            f"(가격: {best_case['sell_price']} / 수량: {best_case['sell_vol']:.2f})"
        )
        log(log_msg)

        # ==========================================================
        # 1시간마다 한 번만 차익 알림 전송
        # ==========================================================
        if (now_dt - last_alert_time).total_seconds() >= ALERT_COOLDOWN_SECOND:

            arbitrage_telegram_message = (
                f"🚨 [Arbitrage Opportunity]\n"
                f"Direction : {best_case['label']}\n"
                f"Spread    : {best_percent:.2f}%\n\n"
                f"[BUY]\n"
                f"Exchange  : {buy_exchange.value.upper()}\n"
                f"Price     : {best_case['buy_price']}\n"
                f"Volume    : {best_case['buy_vol']:.2f}\n\n"
                f"[SELL]\n"
                f"Exchange  : {sell_exchange.value.upper()}\n"
                f"Price     : {best_case['sell_price']}\n"
                f"Volume    : {best_case['sell_vol']:.2f}"
            )
            log("★ [LOG] Sending Telegram...")
            send_telegram_message(arbitrage_telegram_message)
            log("★ [LOG] Telegram Done")
            last_alert_time = now_dt

        # buy, sell 거래소의 자산 정보를 대입
        buy_exchange_available_usdt = balances[buy_exchange]['free'].get('USDT', 0) # buy_exchange의 Available USDT
        buy_exchange_ask_volume_usdt = (best_case['buy_price'] * best_case['buy_vol']) # buy_exchange의 매도 1호가 물량을 USDT로 환산한 값

        sell_exchange_available_ness = balances[sell_exchange]['free'].get('NESS', 0) # sell_exchange의 Available NESS
        sell_exchange_available_ness_usdt = (sell_exchange_available_ness * best_case['sell_price']) # sell_exchange의 Available NESS를 USDT로 환산한 값
        sell_exchange_bid_volume_usdt = (best_case['sell_price'] * best_case['sell_vol']) # sell_exchange의 매수 1호가 물량을 USDT로 환산한 값

        log("★ [LOG] Asset Check")
        log(f" buy_exchange의 Available USDT : {buy_exchange_available_usdt}")
        log(f" buy_exchange의 매도 1호가 물량을 USDT로 환산한 값 : {buy_exchange_ask_volume_usdt}")
        log(f" sell_exchange의 Available NESS : {sell_exchange_available_ness}")
        log(f" sell_exchange의 Available NESS를 USDT로 환산한 값 : {sell_exchange_available_ness_usdt}")
        log(f" sell_exchange의 매수 1호가 물량을 USDT로 환산한 값 : {sell_exchange_bid_volume_usdt}")

        min_usdt = min(
            buy_exchange_available_usdt,
            buy_exchange_ask_volume_usdt,
            sell_exchange_available_ness_usdt,
            sell_exchange_bid_volume_usdt
            )
        
        
        log(f" Minimum USDT   : {min_usdt}")

        if min_usdt < MIN_TRADE_USDT:
            log(f"★ [LOG] Skip - Tradable USDT too small ({min_usdt:.4f})")
            continue

        tradable_usdt = min_usdt

        log(f" Tradable USDT   : {tradable_usdt}")

        buy_trade_amount, sell_trade_amount = calculate_trade_amount(
            tradable_usdt,
            best_case['buy_price'],
            best_case['sell_price']
        )

        log(
            f"★ [LOG] "
            f"Buy Amount={buy_trade_amount}, "
            f"Sell Amount={sell_trade_amount}"
        )

        if buy_trade_amount == 0 or sell_trade_amount == 0:
            log("★ [LOG] Skip - Trade amount is zero")
            continue

        try:
            log("★ [LOG] Sending BUY order...")
            buy_order = exchanges[buy_exchange].create_limit_buy_order(
                SYMBOL,
                buy_trade_amount,
                best_case['buy_price']
            )
            log(
                f"★ [LOG] BUY order sent "
                f"(Order ID: {buy_order.get('id', 'Unknown')})"
            )
        except Exception as e:
            error(f"★ [ERROR] BUY order failed: {e}")
            log("★ [LOG] Sending Telegram...")
            send_telegram_message(
                f"🚨 [BUY ORDER FAILED]\n"
                f"Time     : {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"Exchange : {buy_exchange.value.upper()}\n"
                f"Reason   : {e}"
            )
            log("★ [LOG] Telegram Done")
            continue

        try:
            log("★ [LOG] Sending SELL order...")
            sell_order = exchanges[sell_exchange].create_limit_sell_order(
                SYMBOL,
                sell_trade_amount,
                best_case['sell_price']
            )
            log(
                f"★ [LOG] SELL order sent "
                f"(Order ID: {sell_order.get('id', 'Unknown')})"
            )
        except Exception as e:
            error(f"★ [ERROR] SELL order failed: {e}")
            log("★ [LOG] Sending Telegram...")
            send_telegram_message(
                f"🚨 [SELL ORDER FAILED]\n"
                f"Time         : {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"Exchange : {sell_exchange.value.upper()}\n"
                f"BUY Order ID : {buy_order.get('id', 'Unknown')}\n"
                f"Reason   : {e}\n\n"
                f"⚠️ BUY 주문은 이미 체결되었을 가능성이 있으므로 즉시 확인하세요."
            )
            log("★ [LOG] Telegram Done")
            continue

        after_trading_dt = datetime.now()
        after_trading_str = after_trading_dt.strftime('%Y-%m-%d %H:%M:%S')

        # 주문 완료시 프린트 및 텔레그램 전송 추가
        trade_log = (
            f"✅ [NESS 아비트리지 주문 완료]\n"
            f"시간: {after_trading_str}\n"
            f"방향: {best_case['label']}\n"
            f"차이: {best_percent:.2f}%\n\n"
            f"[매수]\n"
            f"거래소: {buy_exchange.value.upper()}\n"
            f"가격: {best_case['buy_price']}\n"
            f"수량: {buy_trade_amount} NESS\n"
            f"주문금액: {buy_trade_amount * best_case['buy_price']:.4f} USDT\n\n"
            f"[매도]\n"
            f"거래소: {sell_exchange.value.upper()}\n"
            f"가격: {best_case['sell_price']}\n"
            f"수량: {sell_trade_amount} NESS\n"
            f"주문금액: {sell_trade_amount * best_case['sell_price']:.4f} USDT"
        )

        log(trade_log)

        log("★ [LOG] Sending Telegram...")
        send_telegram_message(trade_log)
        log("★ [LOG] Telegram Done")
        
        log("========== LOOP END ==========\n")
        
    except Exception as e:
        error(f"에러 발생: {e}")

    finally:
        # DELAY_SECOND 초에 한 번씩 조회 (과도한 요청으로 인한 IP 차단 방지)
        time.sleep(DELAY_SECOND)