import os
import tempfile
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client, Client
from pdf_reader import parse_grades_from_pdf
from openai import OpenAI

# Flask app
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

# OpenAI設定
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    if "成績" in text or "単位" in text:
        response = supabase.table("grade_text").select("*").eq("user_id", user_id).execute()
        if response.data:
            message = response.data[0]["text"]
        else:
            message = "❌ 成績データが見つかりません。PDFを送ってね！"
    elif any(keyword in text for keyword in ["成績についてのアドバイス", "単位についてのアドバイス", "卒業についてのアドバイス"]):
        # 成績データを取得
        grades_response = supabase.table("grades").select("*").eq("user_id", user_id).execute()
        if grades_response.data:
            grades = grades_response.data
            # AIにアドバイス生成を依頼
            try:
                completion = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "あなたは明治大学の学生をサポートするアドバイザーです。成績状況に基づいてアドバイスしてください。"},
                        {"role": "user", "content": f"以下は学生の成績データです。これに基づいてアドバイスをください:\n{grades}"}
                    ],
                )
                message = completion.choices[0].message.content
            except Exception as e:
                message = f"💡 アドバイス生成に失敗しました: {e}"
        else:
            message = "❌ 成績データが見つかりません。PDFを送ってね！"
    else:
        # 雑談モード
        try:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "あなたは明治大学の学生をサポートするアシスタントです。"},
                    {"role": "user", "content": text},
                ],
            )
            message = completion.choices[0].message.content
        except Exception as e:
            message = f"💡 雑談の生成に失敗しました: {e}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))


@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    user_id = event.source.user_id
    file_name = event.message.file_name

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        file_path = tmp_file.name
        message_content = line_bot_api.get_message_content(event.message.id)
        for chunk in message_content.iter_content():
            tmp_file.write(chunk)

    try:
        grades_text, grades_list = parse_grades_from_pdf(file_path)

        # Supabaseに保存（grades）
        for g in grades_list:
            supabase.table("grades").upsert(
                {
                    "user_id": user_id,
                    "category": g["category"],
                    "earned": g["earned"],
                    "required": g["required"],
                    "note": g.get("note"),  # 👈 備考欄を追加
                }
            ).execute()

        # Supabaseに保存（grade_text）
        supabase.table("grade_text").upsert(
            {"user_id": user_id, "text": grades_text}
        ).execute()

        message = "✅ PDFを保存しました！\n\n" + grades_text

    except Exception as e:
        message = f"❌ PDFの解析に失敗しました: {e}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))


@app.route("/", methods=["GET"])
def index():
    return "LINE Bot is running!"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
