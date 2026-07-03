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
COOLTIME_SECOND = 1800

# 💡 1시간 알림 제한을 위한 시간 저장 변수
# 시작하자마자 기회가 오면 바로 알림을 보낼 수 있도록 초기값은 현재 시간의 '1시간 전'으로 세팅합니다.
last_alert_time = datetime.now() - timedelta(hours=1)

# 💡 직전 매수 차례 거래소를 기억하는 상태 변수
last_buy_exchange = None

# 거래소 객체 생성
bitget = ccxt.bitget()
gateio = ccxt.gate()

start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
print(f"[{start_time}] 🚀 NESS 아비트리지 얼러트 시작...")

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
            print(f"❌ [Telegram] 전송 실패 (코드: {response.status_code})")
    except Exception as e:
        print(f"❌ [Telegram] 에러 발생: {e}")

# ==========================================================
# 4. 메인 실행 루프
# ==========================================================
while True:
    try:
        now_dt = datetime.now()
        now_str = now_dt.strftime('%Y-%m-%d %H:%M:%S')

        # 매수/매도 담당 거래소를 저장할 이넘 변수 초기화
        buy_exchange = None
        sell_exchange = None


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

        # -------------------------------------------------------------
        # 케이스 1: 비트겟에서 사서 ➡️ 게이트에 파는 경우
        # -------------------------------------------------------------
        bg_buy_gate_sell_percent = ((gate_bid - bg_ask) / bg_ask) * 100

        if bg_buy_gate_sell_percent >= DIFFERENT_RATE:
            buy_exchange = Exchange.BITGET # 💡 매수 거래소 이넘 변수 변경
            sell_exchange = Exchange.GATEIO

            log_msg = (f"{now_str}\n"
                       f"[BG ➡️ GATE] 차이: {bg_buy_gate_sell_percent:.2f}%\n"
                       f"매수: {buy_exchange.value.upper()}\n"
                       f"(가격: {bg_ask} / 수량: {bg_ask_vol:.2f})\n"
                       f"매도: {sell_exchange.value.upper()}\n"
                       f"(가격: {gate_bid} / 수량: {gate_bid_vol:.2f})")
            print(log_msg)

        # -------------------------------------------------------------
        # 케이스 2: 게이트에서 사서 ➡️ 비트겟에 파는 경우
        # -------------------------------------------------------------
        gate_buy_bg_sell_percent = ((bg_bid - gate_ask) / gate_ask) * 100

        if gate_buy_bg_sell_percent >= DIFFERENT_RATE:
            buy_exchange = Exchange.GATEIO      # 💡 매수 거래소 이넘 변수 변경
            sell_exchange = Exchange.BITGET

            log_msg = (f"{now_str}\n"
                       f"[GATE ➡️ BG] 차이: {gate_buy_bg_sell_percent:.2f}%\n"
                       f"매수: {buy_exchange.value.upper()}\n"
                       f"(가격: {gate_ask} / 수량: {gate_ask_vol:.2f})\n"
                       f"매도: {sell_exchange.value.upper()}\n"
                       f"(가격: {bg_bid} / 수량: {bg_bid_vol:.2f})")
            print(log_msg)

        # -------------------------------------------------------------
        # 💡 텔레그램 알림 발송 조건 체크 (1시간 쿨다운 + 거래소 변경 감지)
        # -------------------------------------------------------------
        # buy_exch가 변경되었다는 것은 갭 조건이 충족되었다는 뜻
        if buy_exchange is not None:
            # 현재 시간과 마지막으로 알림을 보낸 시간의 차이를 계산
            time_passed = now_dt - last_alert_time

            # 직전 알림과 현재 매수 거래소 방향이 달라졌는가?
            is_exchange_changed = (last_buy_exchange != buy_exchange)

            # 시간이 1시간(COOLTIME_SECOND 초) 이상 지났을 때만 알림 전송
            if time_passed.total_seconds() >= COOLTIME_SECOND or is_exchange_changed:

                # [추가] 거래소가 바뀐 조기 알림이라면 문구 변경
                prefix = "🔄 [방향 전환] " if is_exchange_changed and time_passed.total_seconds() < COOLTIME_SECOND else "🚨 "
                alert_text = f"{prefix}[NESS 아비트리지 발생]\n{log_msg}"

                send_telegram_message(alert_text)

                # 상태값 변수들 최신화
                last_alert_time = now_dt
                last_buy_exchange = buy_exchange

                print(f"📱 [Telegram] 1시간 제한 적용 - 알림 발송 완료 (다음 알림 가능 시간: {(now_dt + timedelta(hours=1)).strftime('%H:%M:%S')})")
            else:
                # COOLTIME_SECOND 시간이 안 지났다면 로그만 찍고 텔레그램은 패스
                remaining_time = COOLTIME_SECOND - time_passed.total_seconds()
                print(f"⏳ [Telegram] 알림 쿨다운 중... (남은 시간: {int(remaining_time)//60}분 {int(remaining_time)%60}초)")
            
            print("----------------------------------------------------------------------------")

        # 10초에 한 번씩 조회 (과도한 요청으로 인한 IP 차단 방지)
        time.sleep(DELAY_SECOND)
        
    except Exception as e:
        print(f"에러 발생: {e}")
        time.sleep(DELAY_SECOND)