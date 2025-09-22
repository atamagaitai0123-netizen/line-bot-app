import os
import tempfile
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client, Client
from pdf_reader import parse_grades_from_pdf
from openai import OpenAI

# 環境変数
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 初期化
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)


def save_grades_to_db(user_id, grades):
    for g in grades:
        supabase.table("grades").insert({
            "user_id": user_id,
            "category": g["category"],
            "earned": g["earned"],
            "required": g["required"]
        }).execute()


def check_graduation_status(user_id):
    result = supabase.table("grades").select("*").eq("user_id", user_id).execute()
    rows = result.data
    if not rows:
        return "❌ 成績データがまだ登録されていません。PDFを送ってください。"

    messages = ["📊 成績状況:"]
    for row in rows:
        earned = row.get("earned", 0)
        required = row.get("required", 0)
        remaining = max(0, required - earned) if required else 0
        messages.append(f"{row['category']}: {earned}/{required} (残り{remaining}単位)")
    return "\n".join(messages)


# 📘 Supabase 便覧検索
def search_curriculum_info(query: str):
    try:
        result = supabase.table("curriculum").select("*").ilike("notes", f"%{query}%").execute()
        if result.data:
            info_texts = [f"- {row['category']}: {row['notes']}" for row in result.data]
            return "\n".join(info_texts)
    except Exception as e:
        print(f"Supabase curriculum search error: {e}")
    return None


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    if event.message.file_name.endswith(".pdf"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            file_path = tmp_file.name
            message_content = line_bot_api.get_message_content(event.message.id)
            for chunk in message_content.iter_content():
                tmp_file.write(chunk)

        try:
            grades = parse_grades_from_pdf(file_path)
            save_grades_to_db(event.source.user_id, grades)
            reply_text = "✅ PDFを保存しました！\n" + check_graduation_status(event.source.user_id)
        except Exception as e:
            reply_text = f"❌ PDFの解析に失敗しました: {e}"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text.strip()
    user_id = event.source.user_id

    # 成績確認
    if "成績" in user_message or "単位" in user_message:
        reply_text = check_graduation_status(user_id)

    # 🎓 便覧検索を優先
    elif "卒業" in user_message or "履修" in user_message or "要件" in user_message:
        curriculum_info = search_curriculum_info(user_message)
        if curriculum_info:
            system_prompt = f"あなたは大学の履修相談をサポートするアシスタントです。\n以下は大学便覧から見つかった情報です:\n{curriculum_info}"
        else:
            system_prompt = "あなたは大学の履修相談をサポートするアシスタントです。"

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )
        reply_text = response.choices[0].message.content.strip()

    # 💬 雑談（便覧情報も利用可能）
    else:
        curriculum_info = search_curriculum_info(user_message)
        system_prompt = "あなたは学生と雑談もできる大学サポートAIです。"
        if curriculum_info:
            system_prompt += f"\n以下は大学便覧から見つかった情報です:\n{curriculum_info}"

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ]
            )
            reply_text = response.choices[0].message.content.strip()
        except Exception as e:
            reply_text = f"💡 雑談の生成に失敗しました: {e}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
