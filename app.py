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

    # 成績関連の問い合わせ
    if "成績" in text or "単位" in text:
        response = supabase.table("grades_text").select("*").eq("user_id", user_id).execute()
        if response.data:
            message = response.data[0]["content"]  # ✅ 修正済み
        else:
            message = "❌ 成績データが見つかりません。PDFを送ってね！"

    # 成績アドバイス
    elif "成績についてのアドバイス" in text or "単位についてのアドバイス" in text:
        response = supabase.table("grades_text").select("*").eq("user_id", user_id).execute()
        if response.data:
            grades_text = response.data[0]["content"]  # ✅ 修正済み
            try:
                completion = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "あなたは明治大学の学生をサポートするアシスタントです。成績状況に基づいて助言してください。"},
                        {"role": "user", "content": f"以下の成績状況に基づいて、卒業に向けたアドバイスをください。\n\n{grades_text}"},
                    ],
                )
                message = completion.choices[0].message.content
            except Exception as e:
                message = f"💡 アドバイス生成に失敗しました: {e}"
        else:
            message = "❌ 成績データが見つかりません。PDFを送ってね！"

    # 便覧関連（履修条件・卒業要件など）
    elif "履修条件" in text or "卒業要件" in text:
        response = supabase.table("curriculum_docs").select("*").ilike("content", f"%{text}%").execute()
        if response.data:
            message = response.data[0]["content"]
        else:
            message = "❌ 履修条件や卒業要件の情報が見つかりませんでした。"

    # 事務室問い合わせ
    elif "事務室" in text or "電話番号" in text or "問い合わせ" in text:
        response = supabase.table("inquiry_contacts").select("*").execute()
        if response.data:
            contacts = [
                f"{row['department']} ({row['target']}): {row['phone']}\n{row['page_url']}"
                for row in response.data
            ]
            message = "📞 明治大学 各学部事務室の連絡先:\n\n" + "\n\n".join(contacts)
        else:
            message = "❌ 事務室の連絡先情報が見つかりませんでした。"

    # 雑談モード
    else:
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
        # pdf_reader が文字列を返す仕様
        grades_text = parse_grades_from_pdf(file_path)

        # Supabaseに保存
        supabase.table("grades_text").upsert(
            {
                "user_id": user_id,
                "content": grades_text,  # ✅ 修正済み
            }
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
