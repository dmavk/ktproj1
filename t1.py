import os
import json
import requests
from flask import Flask, request, jsonify, Response
from aws_sns_message_validator import AWSMessageValidator  # 비공식 0.0.5 버전 기준

from aws_sns_message_validator import MessageValidator

validator = MessageValidator()
validator.validate(message)   # True/False 또는 예외 발생

# git clone https://github.com/wlwg/aws-sns-message-validator.git
#
# cd aws-sns-message-validator-master
#
# # venv 안에서 수동 설치
# pip install .


app = Flask(__name__)
validator = AWSMessageValidator()

# 검증 우회용 환경변수 (테스트 시 사용)
BYPASS = os.getenv("BYPASS_SNS_SIGNATURE_FOR_TEST") == "1"

@app.get("/health")
def health():
    return jsonify(ok=True)

@app.route("/sns", methods=["POST"])
def sns_handler():
    raw = request.get_data(as_text=True)
    try:
        msg = json.loads(raw)
    except Exception:
        return Response("Invalid JSON", status=400)

    mtype = request.headers.get("x-amz-sns-message-type", msg.get("Type"))

    # ✅ 서명 검증 (우회 옵션 제공)
    if not BYPASS:
        try:
            validator.validate(msg)
        except Exception as e:
            app.logger.warning("SNS signature validation failed: %s", str(e))
            return Response("forbidden", status=403)

    # ✅ SNS 구독 확인
    if mtype == "SubscriptionConfirmation":
        subscribe_url = msg.get("SubscribeURL")
        if subscribe_url:
            try:
                requests.get(subscribe_url, timeout=10)
                app.logger.info("✅ Subscription confirmed.")
                return "ok"
            except Exception as e:
                app.logger.error("Subscription confirmation failed: %s", str(e))
                return Response("subscription failed", status=500)
        return Response("missing SubscribeURL", status=400)

    # ✅ Notification 수신 처리
    if mtype == "Notification":
        payload = msg.get("Message", "")
        try:
            parsed = json.loads(payload) if isinstance(payload, str) else payload
        except Exception:
            parsed = {"raw": payload}
        app.logger.info("📩 Notification received: %s", parsed)

        # TODO: 여기서 실제 알림 처리 (예: DB 저장, 슬랙 연동 등)

        return "ok"

    # ✅ 그 외 타입 처리
    app.logger.info("Unhandled SNS message type: %s", mtype)
    return "ok"

# ✅ Flask 앱을 직접 실행할 수 있도록
if __name__ == "__main__":
    ssl_cert = "./ssl/fullchain.pem"
    ssl_key = "./ssl/privkey.pem"

    if not os.path.exists(ssl_cert) or not os.path.exists(ssl_key):
        print("❌ SSL 인증서가 존재하지 않습니다.")
        exit(1)

    app.run(
        host="0.0.0.0",
        port=8443,
        ssl_context=(ssl_cert, ssl_key)
    )
