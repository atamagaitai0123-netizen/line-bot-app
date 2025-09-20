from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI
import os

app = Flask(__name__)

# 環境変数からキーを取得
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# OpenAIクライアントを初期化
client = OpenAI(api_key=OPENAI_API_KEY)


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
        # OpenAI API (v1.x 対応)
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "あなたはLINE botです。ユーザーに丁寧に答えてください。"},
                {"role": "user", "content": user_text}
            ],
        )
        reply_text = response.choices[0].message.content.strip()
    except Exception as e:
        reply_text = f"エラーが発生しました: {str(e)}"

    # LINE に返信
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

