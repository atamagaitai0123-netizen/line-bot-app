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

# Hugging Face API を呼び出す関数 (GPT-Neo 1.3B)
def query_huggingface(user_text):
    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    payload = {"inputs": user_text}

    response = requests.post(
        "https://api-inference.huggingface.co/models/EleutherAI/gpt-neo-1.3B",
        headers=headers,
        json=payload
    )

    if response.status_code == 200:
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0].get("generated_text", "返答が生成できませんでした。")
        else:
            return "返答の形式が不明です。"
    else:
        return f"HuggingFace APIエラー: {response.status_code} - {response.text}"


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


# テキストメッセージ受信時の動作
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text

    try:
        reply_text = query_huggingface(user_text)
    except Exception as e:
        reply_text = f"エラーが発生しました: {str(e)}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)




