from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
import requests

app = Flask(__name__)

# 環境変数からキーを取得
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
HF_API_KEY = os.environ.get("HUGGINGFACE_API_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Hugging Face API のエンドポイント
HF_API_URL = "https://api-inference.huggingface.co/models/facebook/blenderbot-400M-distill"


@app.route("/callback", methods=["POST"])
def callback():
    # X-Line-Signature ヘッダーを取得
    signature = request.headers["X-Line-Signature"]

    # リクエストボディを取得
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# LINEで受け取ったテキストに応答
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text

    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    payload = {"inputs": user_text}

    try:
        response = requests.post(HF_API_URL, headers=headers, json=payload)
        if response.status_code == 200:
            data = response.json()
            reply_text = data[0]["generated_text"]
        else:
            reply_text = f"HuggingFace APIエラー: {response.status_code} - {response.text}"
    except Exception as e:
        reply_text = f"エラーが発生しました: {str(e)}"

    # LINEに返信
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

