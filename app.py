import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage

from supabase import create_client
import pdf_reader

# Flask
app = Flask(__name__)

# LINE API
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Supabase 接続
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========== Supabase 保存 ==========
def save_grades(user_id, results):
    for category, info in results.items():
        supabase.table("grades").insert({
            "user_id": user_id,
            "category": category,
            "required": info["必要"],
            "earned": info["取得"]
        }).execute()


# ========== Supabase 参照 ==========
def get_remaining(user_id):
    data = supabase.table("grades").select("*").eq("user_id", user_id).execute()
    if not data.data:
        return "まだ成績データが登録されていません。PDFを送ってください。"

    messages = []
    for row in data.data:
        req = row["required"]
        got = row["earned"]
        if got is None:
            continue
        if got < req:
            messages.append(f"{row['category']}：あと {req - got} 単位")
    if not messages:
        return "すべての要件を満たしています 🎉"
    return "\n".join(messages)


# ========== LINE Webhook ==========
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'


# ========== メッセージイベント ==========
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    if "あと何単位" in text:
        reply = get_remaining(user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="PDFを送ってください📄"))


# ========== ファイル(PDF)イベント ==========
@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    user_id = event.source.user_id
    message_content = line_bot_api.get_message_content(event.message.id)

    file_path = f"/tmp/{event.message.file_name}"
    with open(file_path, "wb") as f:
        for chunk in message_content.iter_content():
            f.write(chunk)

    try:
        # PDF解析
        results, formatted_text = pdf_reader.check_pdf(file_path, 0)

        # Supabase 保存
        save_grades(user_id, results)

        # LINEに結果を返答
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=formatted_text))

    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"PDF解析エラー: {e}"))


# ========== Render 実行用 ==========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
