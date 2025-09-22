import os
import tempfile
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client, Client
from pdf_reader import parse_grades_from_pdf, check_pdf

app = Flask(__name__)

# LINE設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Supabase設定（オプション）
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Supabaseが設定されている場合のみ初期化
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- 成績関連 ---
def save_grades(user_id, parsed_result):
    """成績データをSupabaseに保存（Supabase設定済みの場合のみ）"""
    if not supabase:
        return False
    
    try:
        # そのユーザーの既存データを削除
        supabase.table("grades").delete().eq("user_id", user_id).execute()

        rows = []
        for item in parsed_result:
            rows.append({
                "user_id": user_id,
                "category": item["category"],
                "required": item["required"],
                "earned": item["earned"]
            })
        if rows:
            supabase.table("grades").insert(rows).execute()
        return True
    except Exception as e:
        print(f"Supabase保存エラー: {str(e)}")
        return False

def get_latest_grades(user_id):
    """ユーザーの最新成績を取得"""
    if not supabase:
        return []
    
    try:
        response = supabase.table("grades").select("*").eq("user_id", user_id).execute()
        return response.data if response.data else []
    except Exception:
        return []

def get_curriculum():
    """カリキュラム情報を取得"""
    if not supabase:
        return []
    
    try:
        response = supabase.table("curriculum").select("*").execute()
        return response.data if response.data else []
    except Exception:
        return []

def check_graduation_status(user_id):
    """卒業要件の確認"""
    grades = get_latest_grades(user_id)
    curriculum = get_curriculum()

    if not grades:
        return []

    results = []
    seen = set()
    for rule in curriculum:
        if rule["category"] in seen:
            continue
        seen.add(rule["category"])

        g = next((x for x in grades if x["category"] == rule["category"]), None)
        earned = g["earned"] if g and g.get("earned") is not None else 0
        required = rule["required_units"] if rule.get("required_units") is not None else 0

        results.append({
            "category": rule["category"],
            "earned": earned,
            "required": required,
            "remaining": max(0, required - earned),
            "notes": rule.get("notes", "")
        })
    return results

def format_graduation_status(results):
    """卒業要件状況をフォーマット"""
    if not results:
        return "📊 成績データがありません。"
    
    lines = ["📊 あなたの成績状況まとめ:"]
    for r in results:
        lines.append(
            f"{r['category']}: {r['earned']}/{r['required']} "
            f"(残り{r['remaining']}単位)"
        )
    return "\n".join(lines)

# --- LINEイベントハンドラ ---
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
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    # 楽単フォームリンク
    if any(k in text for k in ["楽単", "おすすめ授業", "取りやすい授業"]):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📮 楽単情報はこちらから投稿してね！\nhttps://docs.google.com/forms/d/e/1FAIpQLSfw654DpwVoSexb3lI8WLqsR6ex1lRYEX_6Yg1g-S57tw2JBQ/viewform?usp=header"
            )
        )
        return

    # 成績確認
    if any(k in text for k in ["成績", "卒業", "単位", "不足", "あと何"]):
        if supabase:
            status = check_graduation_status(user_id)
            if status:
                reply = format_graduation_status(status)
            else:
                reply = "⚠️ まだ成績データが登録されていません。PDFを送ってね！"
        else:
            reply = "⚠️ 成績データの保存機能が無効です。PDFを送ると解析結果を表示します。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # デフォルト応答
    line_bot_api.reply_message(
        event.reply_token, 
        TextSendMessage(text="❓成績表のPDFを送るか、「成績」「単位」などとメッセージしてね！")
    )

@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    user_id = event.source.user_id
    message_content = line_bot_api.get_message_content(event.message.id)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        for chunk in message_content.iter_content():
            tmp_file.write(chunk)
        tmp_path = tmp_file.name

    try:
        # 詳細なレポートを生成
        detailed_report = check_pdf(tmp_path)
        
        if "PDF解析エラー" not in detailed_report:
            # 解析成功
            reply = "✅ 成績表を解析しました！\n\n" + detailed_report
            
            # Supabase保存も試行（設定されている場合）
            if supabase:
                try:
                    parsed_result = parse_grades_from_pdf(tmp_path)
                    if parsed_result:
                        save_success = save_grades(user_id, parsed_result)
                        if save_success:
                            reply += "\n\n💾 データを保存しました。次回から「成績」で確認できます。"
                        else:
                            reply += "\n\n⚠️ データ保存に失敗しましたが、解析結果は上記の通りです。"
                except Exception as e:
                    print(f"保存処理エラー: {str(e)}")
                    # 解析結果は表示する
        else:
            # 解析失敗
            reply = "❌ PDF解析に失敗しました。明治大学経営学部の成績表PDFを送ってください。"
            
    except Exception as e:
        reply = f"❌ PDFの処理中にエラーが発生しました: {str(e)}"
    finally:
        # 一時ファイルを削除
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# ヘルスチェック用エンドポイント
@app.route("/")
def health():
    return "LINE Bot is running!"

# --- Render用 ---
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
