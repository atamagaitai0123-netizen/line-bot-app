import os
import tempfile
import json
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, DocumentMessage
)
from supabase import create_client, Client
import pdf_reader
from openai import OpenAI

# === Flask アプリ初期化 ===
app = Flask(__name__)

# === LINE API 設定 ===
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# === Supabase 設定 ===
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === OpenAI 設定 ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)


# ===============================
# Webhook エンドポイント
# ===============================
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# ===============================
# PDF (DocumentMessage) の処理
# ===============================
@handler.add(MessageEvent, message=DocumentMessage)
def handle_document_message(event):
    message_content = line_bot_api.get_message_content(event.message.id)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        for chunk in message_content.iter_content():
            tmp_file.write(chunk)
        tmp_path = tmp_file.name

    try:
        grades_text, grades_list = pdf_reader.parse_grades_from_pdf(tmp_path)
    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"❌ PDFの解析に失敗しました: {e}")
        )
        return

    user_id = event.source.user_id

    try:
        supabase.table("grades_text").upsert({
            "user_id": user_id,
            "content": grades_text,   # pdf_reader のテキストそのまま
            "raw_data": grades_list   # JSON 数値データ
        }).execute()
    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"❌ Supabase保存エラー: {e}")
        )
        return

    reply_text = "✅ 成績データを保存しました！\n\n" + grades_text
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


# ===============================
# テキストメッセージの処理
# ===============================
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text.strip()
    user_id = event.source.user_id

    # --- 成績データ再表示 ---
    if "成績" in text and "アドバイス" not in text:
        data = supabase.table("grades_text").select("content").eq("user_id", user_id).execute()
        if data.data:
            reply_text = data.data[0]["content"]
        else:
            reply_text = "まだ成績データが保存されていません。PDFを送ってください。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # --- 成績アドバイス ---
    if "アドバイス" in text:
        data = supabase.table("grades_text").select("raw_data").eq("user_id", user_id).execute()
        if data.data:
            grades_list = data.data[0]["raw_data"]
            try:
                completion = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "あなたは大学の履修アドバイザーです。"},
                        {"role": "user", "content": f"次の成績データに基づいてアドバイスをください:\n{json.dumps(grades_list, ensure_ascii=False)}"}
                    ]
                )
                reply_text = completion.choices[0].message.content
            except Exception as e:
                reply_text = f"❌ アドバイス生成に失敗しました: {e}"
        else:
            reply_text = "まだ成績データが保存されていません。PDFを送ってください。"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # --- 事務室連絡先 ---
    if "事務室" in text and ("電話" in text or "連絡先" in text):
        matched_dept = None
        if "経営" in text:
            matched_dept = "経営"
        elif "商" in text:
            matched_dept = "商"
        elif "法" in text:
            matched_dept = "法"

        if matched_dept:
            query = supabase.table("inquiry_contacts").select("*").ilike("department", f"%{matched_dept}%").execute()
        else:
            query = supabase.table("inquiry_contacts").select("*").limit(10).execute()

        if query.data:
            reply_lines = ["📞 明治大学事務室連絡先:"]
            for row in query.data:
                reply_lines.append(f"{row['department']} {row['target']}: {row['phone']}\n{row['page_url']}")
            reply_text = "\n\n".join(reply_lines)
        else:
            reply_text = "事務室の情報が見つかりませんでした。"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # --- 雑談（Fallback） ---
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "あなたは明治大学の学生をサポートするLINE Botです。"},
                {"role": "user", "content": text}
            ]
        )
        reply_text = completion.choices[0].message.content
    except Exception as e:
        reply_text = f"❌ 雑談の生成に失敗しました: {e}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


# ===============================
# アプリ起動
# ===============================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
