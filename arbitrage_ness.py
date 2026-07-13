from enum import Enum
import ccxt
import time
from datetime import datetime, timedelta
import requests  # 텔레그램 메시지 전송용
import configparser

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

# 💡 1시간 알림 제한을 위한 시간 저장 변수
# 시작하자마자 기회가 오면 바로 알림을 보낼 수 있도록 초기값은 현재 시간의 '1시간 전'으로 세팅합니다.
last_alert_time = datetime.now() - timedelta(hours=1)

# 💡 직전 매수 차례 거래소를 기억하는 상태 변수
last_buy_exchange = None

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

start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
print(f"[{start_time}] 🚀 NESS Arbitrage Start...")

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
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"★ [LOG] ❌ [Telegram] 전송 실패 (코드: {response.status_code})")
    except Exception as e:
        print(f"★ [LOG] ❌ [Telegram] 에러 발생: {e}")

# ==========================================================
# 4. 메인 실행 루프
# ==========================================================
while True:
    try:
        now_dt = datetime.now()
        now_str = now_dt.strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n========== [{now_str}] LOOP START ==========")

        # 매수/매도 담당 거래소를 저장할 이넘 변수 초기화
        buy_exchange = None
        sell_exchange = None

        # 양쪽 거래소 자산 정보 불러오기
        bg_balance = bitget.fetch_balance()
        gate_balance = gateio.fetch_balance()
        print("★ [LOG] Balance fetched successfully")

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

        print(
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
                "label": "BITGET ➡️ GATEIO"
            },
            { # case 2: 게이트에서 사서 ➡️ 비트겟에 파는 경우
                "buy": Exchange.GATEIO,
                "sell": Exchange.BITGET,
                "buy_price": gate_ask,
                "buy_vol": gate_ask_vol,
                "sell_price": bg_bid,
                "sell_vol": bg_bid_vol,
                "label": "GATEIO ➡️ BITGET"
            }
        ]

        for case in cases:
            percent = ((case["sell_price"] - case["buy_price"]) / case["buy_price"]) * 100
            print(
                f"★ [LOG] {case['label']} "
                f"{percent:.3f}%"
            )

            if percent >= DIFFERENT_RATE and percent > best_percent:
                best_percent = percent
                best_case = case

        if best_case is None:
            print("★ [LOG] No arbitrage opportunity")
            time.sleep(DELAY_SECOND)
            continue

        if best_case is not None:
            buy_exchange = best_case["buy"]
            sell_exchange = best_case["sell"]

            log_msg = (
                f"{now_str}\n"
                f"[{best_case['label']}] 차이: {best_percent:.2f}%\n"
                f"매수: {buy_exchange.value.upper()}\n"
                f"(가격: {best_case['buy_price']} / 수량: {best_case['buy_vol']:.2f})\n"
                f"매도: {sell_exchange.value.upper()}\n"
                f"(가격: {best_case['sell_price']} / 수량: {best_case['sell_vol']:.2f})"
            )

            print(log_msg)

        # buy, sell 거래소의 자산 정보를 대입
        buy_exchange_available_usdt = balances[buy_exchange]['free'].get['USDT', 0] # buy_exchange의 Available USDT
        buy_exchange_ask_volume_usdt = (best_case['buy_price'] * best_case['buy_vol']) # buy_exchange의 매도 1호가 물량을 USDT로 환산한 값

        sell_exchange_available_ness = balances[sell_exchange]['free'].get['NESS', 0] # sell_exchange의 Available NESS
        sell_exchange_available_ness_usdt = (sell_exchange_available_ness * best_case['sell_price']) # sell_exchange의 Available NESS를 USDT로 환산한 값
        sell_exchange_bid_volume_usdt = (best_case['sell_price'] * best_case['sell_vol']) # sell_exchange의 매수 1호가 물량을 USDT로 환산한 값

        print("★ [LOG] Asset Check")
        print(f" buy_exchange의 Available USDT                    : {buy_exchange_available_usdt}")
        print(f" buy_exchange의 매도 1호가 물량을 USDT로 환산한 값    : {buy_exchange_ask_volume_usdt}")
        print(f" sell_exchange의 Available NESS                   : {sell_exchange_available_ness}")
        print(f" sell_exchange의 Available NESS를 USDT로 환산한 값  : {sell_exchange_available_ness_usdt}")
        print(f" sell_exchange의 매수 1호가 물량을 USDT로 환산한 값   : {sell_exchange_bid_volume_usdt}")

        min_usdt = min(
            buy_exchange_available_usdt,
            buy_exchange_ask_volume_usdt,
            sell_exchange_available_ness_usdt,
            sell_exchange_bid_volume_usdt
            )
        
        
        print(f" Minimum USDT   : {min_usdt}")

        if min_usdt < 1.5:
            print(f"★ [LOG] Skip - Tradable USDT too small ({min_usdt:.4f})")
            continue

        tradable_usdt = min_usdt

        print(f" Tradable USDT   : {tradable_usdt}")

        exchanges = {
            Exchange.BITGET: bitget,
            Exchange.GATEIO: gateio
        }

        buy_exchange_api = exchanges[buy_exchange]
        sell_exchange_api = exchanges[sell_exchange]

        buy_trade_amount = int(tradable_usdt / best_case['buy_price'])
        sell_trade_amount = int(tradable_usdt / best_case['sell_price'])

        print(
            f"★ [LOG] "
            f"Buy Amount={buy_trade_amount}, "
            f"Sell Amount={sell_trade_amount}"
        )

        if buy_trade_amount == 0 or sell_trade_amount == 0:
            print("★ [LOG] Skip - Trade amount is zero")
            continue

        print("★ [LOG] Sending BUY order...")
        exchanges[buy_exchange].create_limit_buy_order(
            SYMBOL,
            buy_trade_amount,
            best_case['buy_price']
        )
        print("★ [LOG] BUY order sent")
        
        print("★ [LOG] Sending SELL order...")
        exchanges[sell_exchange].create_limit_sell_order(
            SYMBOL,
            sell_trade_amount,
            best_case['sell_price']
        )
        print("★ [LOG] SELL order sent")

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

        print(trade_log)

        print("★ [LOG] Sending Telegram...")
        send_telegram_message(trade_log)
        print("★ [LOG] Telegram Done")
        
        print("========== LOOP END ==========\n")

        # DELAY_SECOND 초에 한 번씩 조회 (과도한 요청으로 인한 IP 차단 방지)
        time.sleep(DELAY_SECOND)
        
    except Exception as e:
        print(f"에러 발생: {e}")
        time.sleep(DELAY_SECOND)