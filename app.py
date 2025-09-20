from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
import os
from openai import OpenAI
from supabase import create_client, Client
from pdf_reader import check_pdf  # 既存のPDF解析コードを利用

app = Flask(__name__)

# 環境変数からキーを取得
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

# Supabase 初期化
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# -------------------------
# 不足単位をチェックする関数
# -------------------------
def get_missing_categories(user_id):
    data = supabase.table("grades").select("*").eq("user_id", user_id).execute()
    if not data.data:
        return "まだ成績データが登録されていません。PDFを送ってください。"

    missing = []
    for row in data.data:
        req = row["required"]
        got = row["earned"]
        if req is None or got is None:
            continue
        if got < req:
            missing.append(f"・{row['category']}: あと {req - got} 単位")

    if not missing:
        return "✅ すべてのカテゴリで要件を満たしています！"
    return "=== 不足している科目区分 ===\n" + "\n".join(missing)


# -------------------------
# Webhook エンドポイント
# -------------------------
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# -------------------------
# LINE メッセージ処理
# -------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text
    user_id = event.source.user_id

    # 特定の質問に対して不足単位を返す
    if any(kw in user_text for kw in ["不足", "足りない", "あと何単位", "何単位足りない"]):
        reply_text = get_missing_categories(user_id)
    else:
        # OpenAI を利用した通常チャット
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "あなたは学生の履修や卒業要件をサポートするAIです。"},
                    {"role": "user", "content": user_text}
                ],
            )
            reply_text = response.choices[0].message.content.strip()
        except Exception as e:
            reply_text = f"エラーが発生しました: {str(e)}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

