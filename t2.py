import os
import json
import requests
from flask import Flask, request, jsonify
from sns_message_validator import SNSMessageValidator

from flask import Response
# git clone https://github.com/wlwg/aws-sns-message-validator.git
#
# cd aws-sns-message-validator-master
#
# # venv 안에서 수동 설치
# pip install .



app = Flask(__name__)
validator = SNSMessageValidator()

# 환경변수로 허용할 TopicArn 화이트리스트 지정 가능
ALLOWED_TOPICS = {t.strip() for t in os.getenv("ALLOWED_SNS_TOPICS", "").split(",") if t.strip()}

def safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None

@app.get("/health")
def health():
    return jsonify(ok=True)

"""
@app.post("/sns")
def sns():
    raw = request.get_data(as_text=True)
    msg = safe_json_loads(raw)
    if not msg or "Type" not in msg:
        app.logger.error("Invalid SNS body: %s", raw[:300])
        return ("bad request", 400)

    # 1. 시그니처 검증
    try:
        validator.validate(msg)
    except Exception as e:
        app.logger.error("SNS signature validation failed: %s", e)
        return ("forbidden", 403)

    # 2. TopicArn 화이트리스트 확인
    topic = msg.get("TopicArn")
    if ALLOWED_TOPICS and topic not in ALLOWED_TOPICS:
        app.logger.error("Unexpected TopicArn: %s", topic)
        return ("forbidden", 403)

    # 3. 메시지 타입 처리
    message_type = request.headers.get("x-amz-sns-message-type", msg.get("Type"))
    app.logger.info("SNS message type: %s", message_type)

    if message_type == "SubscriptionConfirmation":
        subscribe_url = msg.get("SubscribeURL")
        if not subscribe_url:
            return ("missing SubscribeURL", 400)
        try:
            r = requests.get(subscribe_url, timeout=10)
            r.raise_for_status()
            app.logger.info("Subscription confirmed for topic: %s", topic)
            return ("ok", 200)
        except Exception as e:
            app.logger.error("Subscription confirmation failed: %s", e)
            return ("subscription failed", 500)

    elif message_type == "Notification":
        payload = msg.get("Message")
        parsed = safe_json_loads(payload) if isinstance(payload, str) else payload

        app.logger.info("Subject: %s", msg.get("Subject"))
        app.logger.info("Payload: %s", parsed if parsed else payload)

        # TODO: 실제 알림 처리 로직 작성 가능
        return ("ok", 200)

    else:
        app.logger.info("Other SNS type: %s", message_type)
        return ("ok", 200)
"""
# 환경변수 BYPASS_SNS_SIGNATURE_FOR_TEST=1 일 때만 서명 우회
import os
BYPASS = os.getenv("BYPASS_SNS_SIGNATURE_FOR_TEST") == "1"
validator = SNSMessageValidator()

BYPASS = os.getenv("BYPASS_SNS_SIGNATURE_FOR_TEST") == "1"

@app.route("/sns", methods=["POST"])
def sns_handler():
    raw = request.get_data(as_text=True)
    msg = json.loads(raw)
    mtype = request.headers.get("x-amz-sns-message-type", msg.get("Type"))

    # ✅ 서명 검증
    if not BYPASS:
        print('====================')
        try:
            if not validator.is_valid(msg):
                raise ValueError("Invalid SNS signature")
        except Exception as e:
            app.logger.warning("SNS signature validation failed: %s", str(e))
            return Response("forbidden", status=403)

    # ✅ 구독 확인
    if mtype == "SubscriptionConfirmation":
        requests.get(msg["SubscribeURL"], timeout=10)
        app.logger.info("✅ Subscription confirmed")
        return "ok"

    # ✅ 알림 수신
    if mtype == "Notification":
        payload = msg.get("Message", "")
        print('------------------------------')
        try:
            payload_json = json.loads(payload)
        except Exception:
            payload_json = {"raw": payload}
        app.logger.info("📩 SNS Notification received: %s", payload_json)
        return "ok"

    return "ok"


# ✅ 이 아래 부분이 "python app.py" 실행을 가능하게 합니다.
if __name__ == "__main__":
    ssl_cert = "./ssl/fullchain.pem"
    ssl_key = "./ssl/privkey.pem"

    # Flask 앱을 HTTPS 서버로 실행
    app.run(
        host="0.0.0.0",
        port=8443,
        ssl_context=(ssl_cert, ssl_key)
    )


# SSL 인증서가 없다면?
# (A) 테스트용 self-signed 인증서 생성 (로컬 개발 용도)
# mkdir -p ssl
# openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
#   -keyout ssl/privkey.pem -out ssl/fullchain.pem \
#   -subj "/CN=localhost"
#
#
# 이렇게 하면 ssl/privkey.pem과 ssl/fullchain.pem이 생성됩니다.