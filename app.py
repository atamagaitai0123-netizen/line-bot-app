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

# =========================
# ハンドラー
# =========================
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

    # 成績データをそのまま返す
    if "成績" in text or "単位" in text:
        response = supabase.table("grades_text").select("*").eq("user_id", user_id).execute()
        if response.data:
            # 最新の1件だけ返す
            latest_record = sorted(response.data, key=lambda x: x["created_at"], reverse=True)[0]
            message = latest_record["content"]
        else:
            message = "❌ 成績データが見つかりません。PDFを送ってね！"

    # 成績アドバイス
    elif any(k in text for k in ["成績についてのアドバイス", "単位についてのアドバイス", "卒業できる？", "卒業要件"]):
        response = supabase.table("grades_text").select("*").eq("user_id", user_id).execute()
        if response.data:
            latest_record = sorted(response.data, key=lambda x: x["created_at"], reverse=True)[0]
            grades_text = latest_record["content"]
            try:
                completion = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "あなたは明治大学の学生をサポートするアシスタントです。成績データに基づいて具体的にアドバイスしてください。"},
                        {"role": "user", "content": f"以下の成績データに基づいてアドバイスをください:\n{grades_text}"},
                    ],
                )
                message = completion.choices[0].message.content
            except Exception as e:
                message = f"💡 アドバイス生成に失敗しました: {e}"
        else:
            message = "❌ 成績データが見つかりません。PDFを送ってね！"

    # 事務室や便覧情報
    elif any(k in text for k in ["事務室の連絡先", "事務の連絡先", "電話番号", "経営学部の電話番号", "経営の電話番号", "経営学部の事務室の電話番号", "経営学部の問い合わせ"]):
        response = supabase.table("contacts").select("*").execute()
        if response.data:
            info_list = [f"{row['title']}: {row['content']}" for row in response.data]
            message = "\n".join(info_list)
        else:
            message = "❌ 事務室の情報が見つかりません。"

    elif any(k in text for k in ["履修条件", "卒業要件"]):
        response = supabase.table("curriculum_docs").select("*").execute()
        if response.data:
            docs = [f"{row['title']}: {row['content']}" for row in response.data]
            message = "\n".join(docs)
        else:
            message = "❌ 履修条件や卒業要件の情報が見つかりません。"

    # 雑談
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
        # pdf_reader が生成した最終テキストを取得
        grades_text = parse_grades_from_pdf(file_path)

        # Supabaseに保存（最新のみにするため古いデータは削除）
        supabase.table("grades_text").delete().eq("user_id", user_id).execute()
        supabase.table("grades_text").insert(
            {"user_id": user_id, "content": grades_text}
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
