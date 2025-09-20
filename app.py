from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from openai import OpenAI
import os
import tempfile
from pdf_reader import extract_text_from_pdf  # pdf_reader.py を利用

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
def handle_text_message(event):
    user_text = event.message.text

    try:
        # OpenAI API に問い合わせ
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "あなたは学生の履修相談をサポートするAIです。"},
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


# PDFファイル受信時の動作
@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    if event.message.file_name.endswith(".pdf"):
        # 一時ファイルに保存
        message_content = line_bot_api.get_message_content(event.message.id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            for chunk in message_content.iter_content():
                tmp_file.write(chunk)
            tmp_path = tmp_file.name

        try:
            # PDFをテキスト化
            pdf_text = extract_text_from_pdf(tmp_path)

            # ChatGPTに渡す
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "あなたは学生の成績通知書を解析するAIです。履修科目や単位数を要約してください。"},
                    {"role": "user", "content": pdf_text}
                ]
            )
            reply_text = response.choices[0].message.content.strip()
        except Exception as e:
            reply_text = f"PDF処理中にエラーが発生しました: {str(e)}"

        # LINEに返信
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="PDFファイルを送ってください📄")
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
