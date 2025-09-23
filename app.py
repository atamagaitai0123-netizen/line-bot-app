import os
import pdfplumber
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, FileMessage, TextSendMessage
from supabase import create_client, Client
from openai import OpenAI
from pdf_reader import parse_grades_from_pdf

app = Flask(__name__)

# 環境変数
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not all([LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY]):
    raise ValueError("環境変数が設定されていません")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)


@app.route("/")
def index():
    return "LINE Bot is running!"


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# テキストメッセージイベント
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    text = event.message.text

    # 成績データの保存確認
    if "成績" in text and "アドバイス" not in text:
        response = supabase.table("grades_text").select("*").eq("user_id", user_id).execute()
        if response.data:
            message = response.data[0]["content"]
        else:
            message = "❌ 成績データが見つかりません。PDFを送ってください。"

    # 成績アドバイス生成
    elif "成績についてのアドバイス" in text or "単位についてのアドバイス" in text:
        response = supabase.table("grades_text").select("*").eq("user_id", user_id).execute()
        if response.data:
            grades_text = response.data[0]["content"]
            try:
                completion = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "あなたは明治大学の学生をサポートするアシスタントです。"
                                "以下に与える成績状況は解析レポートです。"
                                "そのまま繰り返すのではなく、内容を読み取って卒業に向けた具体的な助言をしてください。"
                            ),
                        },
                        {"role": "user", "content": grades_text},
                    ],
                )
                message = completion.choices[0].message.content  # ✅ 修正済み
            except Exception as e:
                message = f"💡 アドバイス生成に失敗しました: {e}"
        else:
            message = "❌ 成績データが見つかりません。PDFを送ってね！"

    # 事務室への問い合わせ
    elif "事務室の電話番号" in text:
        message = "📞 明治大学商学部事務室: 03-3296-4545"

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
            message = completion.choices[0].message.content  # ✅ 修正済み
        except Exception as e:
            message = f"💡 雑談の生成に失敗しました: {e}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))


# PDF ファイルメッセージイベント
@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    file_path = f"/tmp/{event.message.file_name}"

    # ファイルを保存
    message_content = line_bot_api.get_message_content(message_id)
    with open(file_path, "wb") as f:
        for chunk in message_content.iter_content():
            f.write(chunk)

    # PDF を解析
    grades_text, grades_list = parse_grades_from_pdf(file_path)

    # Supabase に保存
    supabase.table("grades_text").upsert(
        {"user_id": user_id, "content": grades_text}
    ).execute()

    os.remove(file_path)

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 成績データを保存しました！"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
