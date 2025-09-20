from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client, Client
from openai import OpenAI
import os
import tempfile
import pdf_reader

app = Flask(__name__)

# 環境変数
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# LINEのWebhook
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# メッセージ処理
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    user_id = event.source.user_id
    user_text = event.message.text.strip()

    # 「不足してる科目」などを検出
    if any(kw in user_text for kw in ["不足", "あと何単位", "足りない"]):
        try:
            res = supabase.table("grades").select("results_text").eq("user_id", user_id).order("id", desc=True).limit(1).execute()
            if res.data:
                reply_text = res.data[0]["results_text"]
            else:
                reply_text = "まだ成績表が登録されていません。PDFを送ってください。"
        except Exception as e:
            reply_text = f"Supabase取得エラー: {str(e)}"

    else:
        # 通常会話 → OpenAI
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "あなたは学生の履修サポートを行うアシスタントです。"},
                    {"role": "user", "content": user_text}
                ],
            )
            reply_text = response.choices[0].message.content.strip()
        except Exception as e:
            reply_text = f"OpenAIエラー: {str(e)}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


# PDFファイル受信時
@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    user_id = event.source.user_id
    message_content = line_bot_api.get_message_content(event.message.id)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        for chunk in message_content.iter_content():
            tmp_file.write(chunk)
        tmp_path = tmp_file.name

    try:
        # PDF解析
        result_text = pdf_reader.check_pdf(tmp_path, page_no=0, return_dict=False)

        # Supabaseに保存
        supabase.table("grades").insert({
            "user_id": user_id,
            "file_name": event.message.file_name,
            "results_text": result_text
        }).execute()

        reply_text = "成績表を解析しました！\n\n" + result_text
    except Exception as e:
        reply_text = f"PDF解析エラー: {str(e)}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
