import os
import tempfile
import pdf_reader
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage

from supabase import create_client, Client

app = Flask(__name__)

# LINE API設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Supabase設定
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# === イベント処理 ===
@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    message_content = line_bot_api.get_message_content(event.message.id)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        for chunk in message_content.iter_content():
            tmp_file.write(chunk)
        tmp_file_path = tmp_file.name

    try:
        # PDF解析
        result_text, result_dict = pdf_reader.check_pdf(tmp_file_path, return_dict=True)

        # Supabaseへ保存
        file_name = os.path.basename(tmp_file_path)
        supabase.table("grades").insert({
            "user_id": event.source.user_id,   # ここを追加！！
            "file_name": file_name,
            "results_text": result_text
        }).execute()

        # ユーザーへ返信
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="成績表を解析しました！\n\n" + result_text)
        )
    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"PDF解析エラー: {str(e)}")
        )


@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    user_text = event.message.text.strip()

    try:
        # Supabaseから最新の解析結果を取得
        response = supabase.table("grades").select("results_text").eq("user_id", event.source.user_id).order("id", desc=True).limit(1).execute()

        if response.data:
            latest_result = response.data[0]["results_text"]
        else:
            latest_result = None

        if "単位" in user_text or "不足" in user_text:
            if latest_result:
                reply_text = "直近の成績解析に基づく結果です：\n\n" + latest_result
            else:
                reply_text = "まだ成績表が保存されていません。PDFを送ってください。"
        else:
            reply_text = "こんにちは！成績や卒業要件について質問できます。PDFを送ると解析します。"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"エラー: {str(e)}")
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
