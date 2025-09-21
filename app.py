import os
import tempfile
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client
import pdf_reader
import openai

# ============ 初期化 ============
app = Flask(__name__)

# LINE
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# OpenAI
openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ============ Supabase ユーティリティ ============
def save_grades(user_id, parsed_result):
    """
    parsed_result = [
      {"category": "学部必修科目区分", "required": 12, "earned": 12},
      {"category": "教養科目区分", "required": 24, "earned": 18},
      ...
    ]
    """
    rows = []
    for item in parsed_result:
        rows.append({
            "user_id": user_id,
            "category": item["category"],
            "required": item["required"],
            "earned": item["earned"]
        })
    supabase.table("grades").insert(rows).execute()

def get_latest_grades(user_id):
    response = supabase.table("grades") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .limit(50) \
        .execute()
    return response.data

# ============ OpenAI ユーティリティ ============
def ask_openai(prompt):
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()

# ============ Flask ルーティング ============
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

# ============ LINE ハンドラ ============
# PDF ファイル受信
@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    user_id = event.source.user_id
    message_content = line_bot_api.get_message_content(event.message.id)

    # 一時ファイルに保存
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        for chunk in message_content.iter_content():
            temp_file.write(chunk)
        temp_file_path = temp_file.name

    # PDF解析 (dict形式で取得)
    parsed = pdf_reader.check_pdf(temp_file_path, return_dict=True)

    if "error" in parsed:
        reply_text = f"PDF解析エラー: {parsed['error']}"
    else:
        # Supabase保存用に results をリスト化
        parsed_result = []
        for cat, (earned, required) in parsed["results"].items():
            parsed_result.append({
                "category": cat,
                "required": required,
                "earned": earned
            })

        save_grades(user_id, parsed_result)
        reply_text = "PDFを受け取りました。成績データを保存しました！"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# テキストメッセージ受信
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text

    grades = get_latest_grades(user_id)

    if grades:
        grades_text = "\n".join(
            [f"{g['category']}: {g['earned']}/{g['required']}" for g in grades]
        )
        prompt = f"""
        以下はユーザーの最新の成績状況です:
        {grades_text}

        ユーザーの質問: {text}
        このデータを基に答えてください。
        """
        answer = ask_openai(prompt)
    else:
        answer = "まだ成績データが登録されていません。PDFを送ってください。"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=answer)
    )

# ヘルスチェック用
@app.route("/health")
def health():
    return "OK", 200

# ============ メイン ============
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
