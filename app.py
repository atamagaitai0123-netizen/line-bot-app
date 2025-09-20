from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
import os
from supabase import create_client, Client
from pdf_reader import check_pdf  # PDF解析用
import tempfile

# Flaskアプリ
app = Flask(__name__)

# 環境変数からキーを取得
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Supabaseクライアント
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========== PDF受信時 ==========
@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    if not event.message.file_name.endswith(".pdf"):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="PDFファイルを送ってください📄")
        )
        return

    # 一時ファイルに保存
    message_content = line_bot_api.get_message_content(event.message.id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        for chunk in message_content.iter_content():
            tmp_file.write(chunk)
        tmp_path = tmp_file.name

    try:
        # PDF解析
        result_text,不足データ = check_pdf(tmp_path)

        # Supabaseに最新データを保存（常に上書き）
        user_id = event.source.user_id
        supabase.table("grades").upsert({
            "user_id": user_id,
            "result_text": result_text,
            "lack_data": 不足データ  # 不足情報をJSON形式で保存
        }).execute()

        reply_text = "📊 PDFを解析しました！\n\n" + result_text
    except Exception as e:
        reply_text = f"PDF解析エラー: {str(e)}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


# ========== テキスト受信時 ==========
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text
    user_id = event.source.user_id
    reply_text = None

    # Supabaseから最新データを取得
    response = supabase.table("grades").select("*").eq("user_id", user_id).order("id", desc=True).limit(1).execute()
    if response.data:
        latest = response.data[0]
        result_text = latest.get("result_text", "")
        lack_data = latest.get("lack_data", {})

        # 「あと何単位？」と聞かれた場合
        if "あと" in user_text or "不足" in user_text or "単位" in user_text:
            不足リスト = []
            for key, lack in lack_data.items():
                不足リスト.append(f"・{key}: あと {lack} 単位")
            reply_text = "=== 不足している科目区分 ===\n" + "\n".join(不足リスト)

    # 通常の返答
    if not reply_text:
        reply_text = "こんにちは！どのようにお手伝いしましょうか？\n成績PDFを送っていただければ解析できます📄"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


# ========== Flask起動 ==========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
