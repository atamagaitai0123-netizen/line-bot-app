# app.py — 修正版（検証済み）
import os
import sys
import json
import tempfile
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client, Client
from openai import OpenAI
import pdf_reader  # あなたが提供している pdf_reader.py を使う想定

# ---- 初期化 ----
app = Flask(__name__)

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not all([LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY]):
    raise ValueError("環境変数が不足しています。LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY を確認してください")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)


# ---- ヘルパー関数 ----
def debug_log(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def safe_reply(reply_token, text):
    try:
        line_bot_api.reply_message(reply_token, TextSendMessage(text=text))
    except LineBotApiError as e:
        debug_log("LineBotApiError replying:", e)
    except Exception as e:
        debug_log("Unexpected error replying:", e)


def call_openai_chat(messages, model="gpt-4o-mini"):
    """
    OpenAI 呼び出し。戻り値の構造差に頑強に対応して文字列を返す（失敗時 None）。
    """
    try:
        resp = client.chat.completions.create(model=model, messages=messages)
        # 互換的取り出し
        try:
            choice = resp.choices[0]
            msg = getattr(choice, "message", None)
            if msg is None and isinstance(choice, dict):
                msg = choice.get("message")
            if isinstance(msg, dict):
                content = msg.get("content") or msg.get("text")
            else:
                content = getattr(msg, "content", None) or getattr(choice, "text", None)
        except Exception:
            # dict-style fallback
            try:
                choice = resp["choices"][0]
                msg = choice.get("message") if isinstance(choice, dict) else None
                if isinstance(msg, dict):
                    content = msg.get("content") or msg.get("text")
                else:
                    content = choice.get("text") or str(resp)
            except Exception:
                content = str(resp)
        if content is None:
            content = str(resp)
        return content
    except Exception as e:
        debug_log("OpenAI call error:", e)
        return None


def fetch_saved_grades(user_id):
    """
    Supabase から最新の成績レコードを取得して (content, raw_data) を返す。
    見つからなければ (None, None)
    """
    try:
        res = supabase.table("grades_text").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
        if res and getattr(res, "data", None):
            row = res.data[0]
            content = row.get("content")
            raw = row.get("raw_data")
            # content が JSON 文字列になっている古いケースに対応
            if isinstance(content, str):
                s = content.strip()
                if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
                    try:
                        parsed = json.loads(content)
                        if raw is None:
                            raw = parsed
                    except Exception:
                        pass
            return content, raw
    except Exception as e:
        debug_log("Supabase fetch error:", e)
    return None, None


def json_to_human(parsed):
    """raw_data(list) を簡易的に人向けのテキストに変換"""
    try:
        if not parsed:
            return ""
        lines = ["📊 === 単位取得状況分析結果 ==="]
        total_obtained = 0
        total_required = 0
        for item in parsed:
            cat = item.get("category") or item.get("name") or "項目"
            earned = item.get("earned", "?")
            required = item.get("required", "?")
            ok = ""
            if isinstance(earned, (int, float)) and isinstance(required, (int, float)):
                ok = " ✅ 完了" if earned >= required else ""
                total_obtained += earned
                total_required += required
            lines.append(f"{cat} {earned}/{required}{ok}")
        lines.append("")
        lines.append(f"卒業必要単位数(参考): {total_required}")
        lines.append(f"取得済み合計(参考): {total_obtained}")
        return "\n".join(lines)
    except Exception as e:
        debug_log("json_to_human error:", e)
        return str(parsed)


def normalize_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return s.strip().lower()


# ---- ルート ----
@app.route("/")
def index():
    return "LINE Bot is running!"


# ---- Webhook ----
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    debug_log("Webhook received (truncated):", body[:1000])
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        debug_log("Invalid signature for webhook")
        abort(400)
    except Exception as e:
        debug_log("handler.handle threw:", e)
        abort(500)
    return "OK"


# ---- テキストメッセージ ----
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    try:
        user_id = event.source.user_id
        text_raw = event.message.text or ""
        debug_log(f"TextMessage from {user_id}: {text_raw}")
        text = normalize_text(text_raw)

        wants_advice = any(k in text for k in ["アドバイス".lower(), "助言".lower(), "advice"])
        wants_grades_check = any(k in text for k in ["成績", "単位", "成績確認"])
        asks_office = any(k in text for k in ["事務室", "連絡先", "電話番号", "電話"])
        wants_easy_class = any(k in text for k in ["楽単", "ラク単", "らくたん", "easy class"])
        
        # 学部判定（簡易）
        dept_keywords = {
            "経営": ["経営", "経営学部"],
            "商学": ["商学", "商学部"],
            "法学": ["法学", "法学部"],
        }
        matched_dept = None
        for key, variants in dept_keywords.items():
            for v in variants:
                if v.lower() in text:
                    matched_dept = key
                    break
            if matched_dept:
                break
        # 0) 楽単フォーム
        if wants_easy_class:
            debug_log("handling: easy class form")
            form_url = "https://docs.google.com/forms/d/e/1FAIpQLSfw654DpwVoSexb3lI8WLqsR6ex1lRYEX_6Yg1g-S57tw2JBQ/viewform?usp=header"
            safe_reply(event.reply_token, f"📝 楽単情報の投稿はこちらから！\n{form_url}")
            return

        # 1) アドバイス要求
        if wants_advice:
            debug_log("handling: advice")
            grades_text, grades_list = fetch_saved_grades(user_id)
            if not grades_text and not grades_list:
                safe_reply(event.reply_token, "❌ 成績データが見つかりません。まずはPDFを送ってください。")
                return
            prompt_system = (
                "あなたは明治大学の学生をサポートするアシスタントです。"
                "以下に与える成績状況（文章と構造化データ）を元に、卒業要件の達成状況、"
                "不足単位がある場合の優先度の高い履修提案、履修順序や注意点を具体的に助言してください。"
                "数字は正確に扱ってください。"
                "アドバイスは、要点を得ていて長文にならないようにしてください。"
            )
            user_content = f"成績レポート:\n{grades_text}\n\n構造化データ:\n{json.dumps(grades_list, ensure_ascii=False)}"
            messages = [
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": user_content}
            ]
            ai_text = call_openai_chat(messages)
            if ai_text is None:
                safe_reply(event.reply_token, "💡 アドバイス生成に失敗しました。時間をおいてもう一度試してください。")
            else:
                safe_reply(event.reply_token, ai_text)
            return

        # 2) 成績表示
        if wants_grades_check:
            debug_log("handling: grades check")
            grades_text, grades_list = fetch_saved_grades(user_id)
            if grades_text:
                safe_reply(event.reply_token, grades_text)
            else:
                safe_reply(event.reply_token, "❌ 成績データが見つかりません。PDFを送ってください。")
            return

        # 3) 事務室問い合わせ
        if asks_office:
            debug_log("handling: inquiry contacts")
            try:
                if matched_dept:
                    pattern = f"%{matched_dept}%"
                    res = supabase.table("inquiry_contacts").select("*").ilike("department", pattern).execute()
                else:
                    res = supabase.table("inquiry_contacts").select("*").limit(50).execute()
                if res and getattr(res, "data", None):
                    rows = res.data
                    if matched_dept and len(rows) >= 1:
                        r = rows[0]
                        out = f"📞 {r.get('department')}:\n{r.get('phone')}\n{r.get('page_url') or ''}"
                        safe_reply(event.reply_token, out)
                        return
                    else:
                        lines = []
                        for r in rows[:10]:
                            lines.append(f"{r.get('department')} ({r.get('target')}): {r.get('phone')}\n{r.get('page_url') or ''}")
                        safe_reply(event.reply_token, "📞 明治大学 各学部事務室の連絡先:\n\n" + "\n\n".join(lines))
                        return
                else:
                    safe_reply(event.reply_token, "該当する事務室の連絡先が見つかりませんでした。学部名を教えてください（例: 経営学部）。")
                    return
            except Exception as e:
                debug_log("Supabase inquiry_contacts error:", e)
                safe_reply(event.reply_token, "事務室情報の取得中にエラーが発生しました。後でもう一度お試しください。")
                return

        # 4) Fallback chat（雑談）
        debug_log("handling: fallback chat")
        messages = [
            {"role": "system", "content": "あなたは明治大学の学生をサポートするアシスタントです。"},
            {"role": "user", "content": text_raw}
        ]
        ai_text = call_openai_chat(messages)
        if ai_text is None:
            safe_reply(event.reply_token, "💡 応答の生成に失敗しました。後ほど試してください。")
        else:
            safe_reply(event.reply_token, ai_text)

    except Exception as e:
        debug_log("handle_text_message unexpected error:", e)
        safe_reply(event.reply_token, "予期せぬエラーが発生しました。管理者に問い合わせてください。")


# ---- ファイル（PDFなど）ハンドラ ----
@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    try:
        user_id = event.source.user_id
        msg = event.message
        file_name = getattr(msg, "file_name", None)
        debug_log(f"FileMessage from {user_id} filename={file_name} id={msg.id}")

        # 一時ファイルに保存（拡張子があるならそれを使う）
        suffix = os.path.splitext(file_name)[1] if file_name and os.path.splitext(file_name)[1] else ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = tmp.name
            content = line_bot_api.get_message_content(msg.id)
            for chunk in content.iter_content():
                tmp.write(chunk)

        # PDF解析（pdf_reader が (text, list) を返す想定）
        try:
            parsed = pdf_reader.parse_grades_from_pdf(temp_path)
            if isinstance(parsed, (list, tuple)):
                grades_text = parsed[0] if len(parsed) > 0 else ""
                grades_list = parsed[1] if len(parsed) > 1 else []
            else:
                grades_text = str(parsed)
                grades_list = []
        except Exception as e:
            debug_log("pdf_reader error:", e)
            grades_text = f"❌ PDFの解析に失敗しました: {e}"
            grades_list = []

        # Supabase 保存
        try:
            payload = {"user_id": user_id, "content": grades_text, "raw_data": grades_list}
            supabase.table("grades_text").upsert(payload).execute()
        except Exception as e:
            debug_log("Supabase upsert error:", e)
            safe_reply(event.reply_token, "解析はできましたがデータの保存に失敗しました。管理者に連絡してください。")

        # LINE に解析結果を返信
        reply_text = "✅ 成績データを保存しました！\n\n" + (grades_text or "（解析結果が空です）")
        safe_reply(event.reply_token, reply_text)

        # 一時ファイル削除
        try:
            os.remove(temp_path)
        except Exception:
            pass

    except Exception as e:
        debug_log("handle_file_message unexpected error:", e)
        safe_reply(event.reply_token, "ファイルの処理中にエラーが発生しました。もう一度送ってください。")


# ---- 起動 ----
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug_log("Starting app on port", port)
    app.run(host="0.0.0.0", port=port)
