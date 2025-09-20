# pdf_reader.py
# 成績PDFから単位取得状況を解析して卒業要件をチェックするスクリプト
# 必要: pip install pdfplumber
# 使い方:
#   text = check_pdf("成績.pdf")                  # フォーマット済テキストを返す
#   text, lack = check_pdf("成績.pdf", return_dict=True)  # テキストと不足辞書を返す

from pathlib import Path
import re
from typing import Dict, Any, Tuple, List, Optional

try:
    import pdfplumber
except Exception as e:
    raise ImportError("pdfplumber が必要です。`pip install pdfplumber` を実行してください。") from e

# -------------------------
# 要件（必要単位） - 必要に応じて編集
# -------------------------
GRAD_REQUIREMENTS = {
    "学部必修科目区分": 12,
    "教養科目区分": 24,
    "外国語科目区分": 16,
    "体育実技科目区分": 2,
    "経営学科基礎専門科目": 14,
    "経営学科専門科目": 32,
    "自由履修科目": 24,
    "合計": 124,
}

# 備考で確認する必修の内訳
SUB_REQUIREMENTS = {
    "英語（初級）": 4,
    "初習外国語": 8,
    "外国語を用いた科目": 4,
}

# 自由履修に含める出所カテゴリ（表記揺れを考慮した正規表現パターンで探す）
FREE_ELECTIVE_PATTERNS = [
    r"実習関連科目",
    r"ICTリテラシー科目",
    r"演習科目\(演習I\)",
    r"演習科目\(演習IIA~IIIB\)",
    r"演習科目",  # 緩く拾う
    r"全学共通総合講座",
    r"国際教育プログラム科目",
    r"グローバル人材育成プログラム科目",
    r"他学部科目",
]

# 年度と思われる数値の閾値（>=2000 を年度として除外）
YEAR_THRESHOLD = 2000
# 単位として現実的に許容する範囲（例: 0〜30 単位）
MIN_CREDIT = 0
MAX_CREDIT = 30

YEARS_ORDER = ['25', '24', '23', '22']


# -------------------------
# ヘルパー関数
# -------------------------
def normalize(s: Optional[str]) -> str:
    if s is None:
        return ""
    return re.sub(r'\s+', '', str(s))


def extract_lines_from_page(page, line_tol: int = 6) -> List[Dict[str, Any]]:
    """
    pdfplumber の extract_words を利用して論理行にまとめる。
    戻り値: [{'text': ..., 'first_x': x0, 'top': top, 'words': [...], 'nums': [...]}]
    """
    words = page.extract_words()
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: (w['top'], w['x0']))
    lines = []
    cur_top = words_sorted[0]['top']
    cur_words = []
    for w in words_sorted:
        if abs(w['top'] - cur_top) <= line_tol:
            cur_words.append(w)
        else:
            cur_words_sorted = sorted(cur_words, key=lambda x: x['x0'])
            text = " ".join(wd['text'] for wd in cur_words_sorted)
            nums = re.findall(r'\d+', text)
            lines.append({'text': text, 'first_x': cur_words_sorted[0]['x0'],
                          'top': cur_top, 'nums': nums, 'words': cur_words_sorted})
            cur_top = w['top']
            cur_words = [w]
    if cur_words:
        cur_words_sorted = sorted(cur_words, key=lambda x: x['x0'])
        text = " ".join(wd['text'] for wd in cur_words_sorted)
        nums = re.findall(r'\d+', text)
        lines.append({'text': text, 'first_x': cur_words_sorted[0]['x0'],
                      'top': cur_top, 'nums': nums, 'words': cur_words_sorted})
    return lines


def _filter_unit_numbers(nums: List[str]) -> List[int]:
    """
    抽出した数字リストから「単位として意味を持つ数値」を返す。
    - 年度（>= YEAR_THRESHOLD）は除外
    - 範囲外の数（> MAX_CREDIT等）は除外
    """
    out = []
    for s in nums:
        try:
            v = int(s)
        except Exception:
            continue
        if v >= YEAR_THRESHOLD:
            continue
        if v < MIN_CREDIT or v > MAX_CREDIT:
            continue
        out.append(v)
    return out


def extract_credit_by_label(text: str) -> Optional[int]:
    """
    まず「(\d+)単位」という形式を探す（これを最優先）。
    無ければ行の中の適切な小さい数値を返す（_filter_unit_numbers参照）。
    戻り値は単位数（int）か None。
    """
    # 優先: 「数字 + 単位」
    m = re.findall(r'(\d+)\s*単位', text)
    if m:
        # 複数見つかる場合は最後のもの（合計や右端の合計を想定）を使う
        vals = [int(x) for x in m if int(x) < YEAR_THRESHOLD and MIN_CREDIT <= int(x) <= MAX_CREDIT]
        if vals:
            return vals[-1]
    # 次に、行内の小さい数値（年度除外）を探す
    filtered = _filter_unit_numbers(re.findall(r'\d+', text))
    if filtered:
        return filtered[-1]
    return None


def find_keyword_rows(lines: List[Dict[str, Any]], keywords: List[str]) -> List[Dict[str, Any]]:
    """
    lines の中からキーワード（完全一致または部分一致）を含む行を抽出。
    keywords は表記そのままのリスト。戻り値は行オブジェクトに 'credit' を追加。
    """
    res = []
    for ln in lines:
        txt = ln['text']
        for kw in keywords:
            if normalize(kw) in normalize(txt):
                credit = extract_credit_by_label(txt)
                r = dict(ln)
                r['credit'] = credit
                res.append(r)
                break
    # 重複（top, first_x, text）を削除
    uniq = []
    seen = set()
    for r in res:
        key = (r['top'], r['first_x'], r['text'])
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


# -------------------------
# メイン: check_pdf
# -------------------------
def check_pdf(pdf_path: str, page_no: int = 0, return_dict: bool = False) -> Any:
    """
    pdf_path: PDFファイルパス
    page_no: 解析するページ番号（デフォルト0）
    return_dict: True の場合、(formatted_text, lack_dict) を返す
    """
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(pdf_path)

    # open and extract logical lines from the target page
    with pdfplumber.open(pdf_path) as pdf:
        if page_no < 0 or page_no >= len(pdf.pages):
            page = pdf.pages[-1]
        else:
            page = pdf.pages[page_no]
        lines = extract_lines_from_page(page, line_tol=6)

    # 検索キーワード群（主要カテゴリ + 備考 + 自由履修ソース）
    keywords = list(GRAD_REQUIREMENTS.keys()) + list(SUB_REQUIREMENTS.keys())
    keywords += [re.sub(r'\\', '', pat) for pat in FREE_ELECTIVE_PATTERNS]
    keywords += ["合計", "総合計"]

    logical_rows = find_keyword_rows(lines, keywords)

    # main_selected: 各カテゴリに対して最良と思われる行（末尾の単位が最大のもの）を選ぶ
    main_selected: Dict[str, Optional[Dict[str, Any]]] = {}
    for key in GRAD_REQUIREMENTS.keys():
        cand = [r for r in logical_rows if normalize(key) in normalize(r['text'])]
        best = None
        best_val = -1
        for c in cand:
            val = c.get('credit')
            if val is None:
                # fallback: 行内数値フィルタで最後の小さい数を使う（parse時と同様）
                val = extract_credit_by_label(c['text'])
            if val is None:
                continue
            if val > best_val:
                best_val = val
                best = {'metrics': {'合計': val}, 'row': c}
        main_selected[key] = best

    # subs: 備考内の必修（英語（初級）等）は単独で探す。単位ラベルがある行を優先。
    sub_results: Dict[str, Dict[str, Any]] = {}
    for sub, req in SUB_REQUIREMENTS.items():
        cand = [r for r in logical_rows if normalize(sub) in normalize(r['text'])]
        if not cand:
            # 該当行がなければ0
            sub_results[sub] = {'req': req, 'got': 0, 'row': None}
            continue
        # 優先: 単位ラベルがあるもの -> 抜き出し値が正しいものを選ぶ
        best_val = None
        best_row = None
        for c in cand:
            val = c.get('credit')
            if val is None:
                val = extract_credit_by_label(c['text'])
            # ignore unrealistic values
            if val is None:
                continue
            if best_val is None or val > best_val:
                best_val = val
                best_row = c
        sub_results[sub] = {'req': req, 'got': int(best_val or 0), 'row': best_row}

    # parsed: 各カテゴリの取得単位（存在しないカテゴリは0）
    parsed: Dict[str, int] = {}
    for key in GRAD_REQUIREMENTS.keys():
        sel = main_selected.get(key)
        got = int(sel['metrics']['合計']) if (sel and sel.get('metrics') and sel['metrics'].get('合計') is not None) else 0
        parsed[key] = got
    # sub結果で上書き（備考欄の数値を優先）
    for sub, info in sub_results.items():
        parsed[sub] = int(info['got'] or 0)

    # --- 自由履修の合算: FREE_ELECTIVE_PATTERNS から行を探して合計する ---
    free_sum = 0
    # 1) まず、成績表に直接「自由履修科目」が載っていて値があればそれを使う（優先）
    direct_free = parsed.get("自由履修科目", 0)
    if direct_free and direct_free > 0:
        parsed["自由履修科目"] = int(direct_free)
    else:
        # 2) 出所カテゴリ群から合算（logical_rows と lines から探す）
        # logical_rows に既に含まれるかチェック
        for pat in FREE_ELECTIVE_PATTERNS:
            for r in logical_rows:
                if re.search(pat, r['text']):
                    v = r.get('credit')
                    if v is None:
                        v = extract_credit_by_label(r['text'])
                    if v is not None:
                        free_sum += int(v)
        # 3) さらに lines（生の行列）を走査して該当パターンの行末数字を拾う（重複に注意）
        #    （logical_rows で拾えない表記揺れに備える）
        seen_texts = set(r['text'] for r in logical_rows)
        for ln in lines:
            txt = ln['text']
            if txt in seen_texts:
                continue
            for pat in FREE_ELECTIVE_PATTERNS:
                if re.search(pat, txt):
                    v = extract_credit_by_label(txt)
                    if v is not None:
                        free_sum += int(v)
                    break
        parsed["自由履修科目"] = int(free_sum or 0)

    # 合計の取得（parsed 中合計が0の場合、合計行を探す）
    total_got = int(parsed.get("合計", 0) or 0)
    if total_got == 0:
        # logical_rows に合計行があるか
        for r in logical_rows:
            if "合計" in r['text'] or "総合計" in r['text']:
                v = r.get('credit')
                if v is None:
                    v = extract_credit_by_label(r['text'])
                if v is not None:
                    total_got = int(v)
                    break
    # fallback: parsed の全カテゴリ和（除外しないと二重計上になる可能性があるので注意）
    if total_got == 0:
        # 合計は主要カテゴリ（合計キーは除く）を合算して算出（自由履修は parsed に反映済）
        total_got = sum(parsed[k] for k in parsed.keys() if k != "合計")

    # -------------------------
    # 不足計算
    # -------------------------
    lack_dict: Dict[str, int] = {}
    known_short_sum = 0

    # main categories（自由履修と合計は後回し）
    for key, req in GRAD_REQUIREMENTS.items():
        if key in ("合計", "自由履修科目"):
            continue
        got = int(parsed.get(key, 0) or 0)
        if got < req:
            lack = req - got
            lack_dict[key] = lack
            known_short_sum += lack

    # subs (備考内必修)
    for sub, req in SUB_REQUIREMENTS.items():
        got = int(parsed.get(sub, 0) or 0)
        if got < req:
            lack = req - got
            lack_dict[sub] = lack
            known_short_sum += lack

    # 合計の不足
    total_req = GRAD_REQUIREMENTS.get("合計", 0)
    total_missing = max(0, total_req - (int(total_got or 0)))
    lack_dict["合計"] = total_missing

    # 自由履修の不足を推定: 合計不足 - 既知不足
    free_missing_est = total_missing - known_short_sum
    if free_missing_est < 0:
        free_missing_est = 0
    # ただし自由履修の所要（GRAD_REQUIREMENTS）と取得(parsed)との差も考慮
    free_have = int(parsed.get("自由履修科目", 0) or 0)
    free_need = GRAD_REQUIREMENTS.get("自由履修科目", 0)
    deduced_free_missing = max(0, free_need - free_have)
    # 最終的に表示する自由履修不足は、推定値と必修差分の最大値
    chosen_free_missing = max(free_missing_est, deduced_free_missing)
    if chosen_free_missing > 0:
        lack_dict["自由履修科目"] = int(chosen_free_missing)

    # -------------------------
    # フォーマット済テキスト生成
    # -------------------------
    out_lines: List[str] = []
    out_lines.append("成績表を解析しました！\n")
    out_lines.append("=== 各カテゴリチェック ===")
    for key, req in GRAD_REQUIREMENTS.items():
        if key == "合計":
            continue
        got = parsed.get(key, 0)
        got_display = "―" if got is None else str(got)
        if got is None:
            status = "❌ データなし"
        else:
            if got < req:
                status = f"❌ 不足 {req - got}"
            else:
                if key == "外国語科目区分":
                    # 備考内の必修が満たされているか確認
                    sub_ng = False
                    for subk in ("英語（初級）", "初習外国語"):
                        if int(parsed.get(subk, 0) or 0) < SUB_REQUIREMENTS.get(subk, 0):
                            sub_ng = True
                    status = "🔺 備考不足あり" if sub_ng else "✅"
                else:
                    status = "✅"
        out_lines.append(f"・{key:<20} 必要={req:<3}  取得={got_display:<3}  {status}")

    # 合計行
    total_status = (f"❌ 不足 {total_req - total_got}" if total_got < total_req else "✅")
    out_lines.append(f"\n合計{'':<15} 必要={total_req:<3}  取得={str(total_got):<3}  {total_status}")

    # 備考（必修科目）
    out_lines.append("\n=== 備考（必修科目） ===")
    for sub, need in SUB_REQUIREMENTS.items():
        got = int(parsed.get(sub, 0) or 0)
        st = "✅" if got >= need else f"❌ 不足 {need - got}"
        out_lines.append(f"{sub:<15} 必要={need:<3}  取得={got:<3}  {st}")

    # 不足一覧（詳細）
    out_lines.append("\n=== 不足している科目区分 ===")
    # main 欠落（自由履修は別途）
    for k in GRAD_REQUIREMENTS.keys():
        if k in ("合計", "自由履修科目"):
            continue
        if k in lack_dict:
            out_lines.append(f"・{k}: あと {lack_dict[k]} 単位")
    # sub 欠落
    for subk in SUB_REQUIREMENTS.keys():
        if subk in lack_dict:
            out_lines.append(f"・{subk}: あと {lack_dict[subk]} 単位")
    # 自由履修（推定）があれば表示
    if "自由履修科目" in lack_dict:
        out_lines.append(f"・自由履修科目: あと {lack_dict['自由履修科目']} 単位")
    out_lines.append(f"・合計: あと {lack_dict.get('合計', 0)} 単位")

    # 総合判定
    ok_main = all(int(parsed.get(k, 0) or 0) >= v for k, v in GRAD_REQUIREMENTS.items() if k != "合計")
    ok_subs = all(int(parsed.get(k, 0) or 0) >= v for k, v in SUB_REQUIREMENTS.items())
    if ok_main and ok_subs and int(total_got or 0) >= total_req:
        out_lines.append("\n🎉 卒業要件を満たしています")
    else:
        out_lines.append("\n❌ 卒業要件を満たしていません")

    formatted_text = "\n".join(out_lines)

    if return_dict:
        # parsed: 各カテゴリの取得値、lack_dict: 各カテゴリの不足
        return formatted_text, {"parsed": parsed, "lack": lack_dict}

    return formatted_text


# 開発用コマンドライン実行
if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "成績.pdf"
    print(check_pdf(path))
