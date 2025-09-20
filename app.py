import os
import tempfile
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client, Client
import pdf_reader

# LINE設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Supabase設定
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

# 最新解析結果を一時保存
last_results = None

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    global last_results

    if not event.message.file_name.endswith(".pdf"):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="PDFファイルを送ってください📄")
        )
        return

    # 一時ファイルに保存
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_path = tmp_file.name
        file_content = line_bot_api.get_message_content(event.message.id)
        for chunk in file_content.iter_content():
            tmp_file.write(chunk)

    try:
        results, results_dict = pdf_reader.check_pdf(tmp_path)

        # 最新結果を保持
        last_results = results_dict

        # Supabaseに保存（過去履歴も残す）
        supabase.table("grades").insert({
            "file_name": event.message.file_name,
            "results_text": results
        }).execute()

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=results)
        )
    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"PDF解析エラー: {str(e)}")
        )
    finally:
        os.remove(tmp_path)

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    global last_results

    user_msg = event.message.text.strip()

    # PDF解析後の不足単位質問に応答
    if last_results and ("単位" in user_msg or "足り" in user_msg):
        reply = "=== 不足している科目区分 ===\n"
        for k, v in last_results.items():
            if v > 0:
                reply += f"・{k}: あと {v} 単位\n"
        total = sum(last_results.values())
        reply += f"・合計: あと {total} 単位\n"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )
    else:
        # 通常の返答
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="PDFを送っていただくと解析できます📑")
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
