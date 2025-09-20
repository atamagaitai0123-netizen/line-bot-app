# pdf_reader.py
# 成績PDFから単位取得状況を解析して卒業要件をチェックするスクリプト
# 必要: pip install pdfplumber

import pdfplumber
import re
from pathlib import Path

# -------------------------
# 卒業要件（プロジェクトと合わせること）
# -------------------------
GRAD_REQUIREMENTS = {
    "学部必修科目区分": 12,
    "教養科目区分": 24,
    "外国語科目区分": 16,
    "体育実技科目区分": 2,
    "経営学科基礎専門科目": 14,
    "経営学科専門科目": 32,
    "自由履修科目": 24,
    "合計": 124
}

# 備考の必修チェック対象（プロジェクトと合わせること）
SUB_REQUIREMENTS = {
    "英語（初級）": 4,
    "初習外国語": 8,
    "外国語を用いた科目": 4
}

YEARS_ORDER = ['25','24','23','22']


# -------------------------
# ヘルパー
# -------------------------
def normalize(s: str) -> str:
    if not s: return ""
    return re.sub(r'\s+', '', s)

def extract_lines_from_page(page, line_tol=6):
    """PDFページから論理行を抽出"""
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

def find_keyword_logical_rows(lines, keywords):
    """キーワードを含む行を抽出（簡易）"""
    logical = []
    for ln in lines:
        words = ln['words']
        n = len(words)
        for i in range(n):
            for width in (1,2,3):
                if i + width > n:
                    continue
                cand = "".join(words[i+j]['text'] for j in range(width))
                for kw in keywords:
                    if normalize(kw) in normalize(cand):
                        sub_words = words[i:]
                        text = " ".join(w['text'] for w in sub_words)
                        nums = re.findall(r'\d+', text)
                        logical.append({'name': text, 'first_x': sub_words[0]['x0'],
                                        'top': ln['top'], 'nums': nums})
                        break
                else:
                    continue
                break
    uniq = []
    seen = set()
    for r in logical:
        key = (r['top'], r['first_x'], r['name'])
        if key not in seen:
            seen.add(key); uniq.append(r)
    return uniq

def parse_nums_to_metrics(nums):
    """数値リストから必要・年度別・合計を抽出"""
    if not nums: return {'必要': None, 'years': {}, '合計': None}
    total = int(nums[-1])
    pre = [int(x) for x in nums[:-1]] if nums[:-1] else []
    need = None
    if len(pre) == len(YEARS_ORDER) + 1:
        need = pre[0]; year_vals = pre[1:]
    elif len(pre) <= len(YEARS_ORDER):
        year_vals = pre
    else:
        need = pre[0]; year_vals = pre[1:]
    years = {}
    for i, v in enumerate(year_vals):
        if i < len(YEARS_ORDER):
            years[YEARS_ORDER[i]] = v
    return {'必要': need, 'years': years, '合計': total}


# -------------------------
# メイン: check_pdf
#   - return_dict=False: 整形テキスト(string) を返す（既存互換）
#   - return_dict=True: (formatted_text, lack_dict) を返す
# -------------------------
def check_pdf(pdf_path, page_no=0, return_dict=False):
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_no]
        lines = extract_lines_from_page(page, line_tol=6)

    keywords = list(GRAD_REQUIREMENTS.keys()) + list(SUB_REQUIREMENTS.keys()) + ["合計","総合計"]
    logical = find_keyword_logical_rows(lines, keywords)
    logical_sorted = sorted(logical, key=lambda r: (r['top'], r['first_x']))

    # main categories 選定
    main_selected = {}
    for key in GRAD_REQUIREMENTS.keys():
        cand = [r for r in logical_sorted if normalize(key) in normalize(r['name'])]
        best = None; best_total = -1
        for c in cand:
            met = parse_nums_to_metrics(c['nums'])
            tot = met['合計'] if met['合計'] is not None else -1
            if tot > best_total:
                best_total = tot; best = {'metrics': met}
        main_selected[key] = best

    # subs
    sub_results = {}
    for sub, req in SUB_REQUIREMENTS.items():
        candidates = [r for r in logical_sorted if normalize(sub) in normalize(r['name'])]
        if not candidates:
            sub_results[sub] = {'req': req, 'got': 0}
            continue
        best = max(candidates, key=lambda c: int(c['nums'][-1]) if c['nums'] else -1)
        met = parse_nums_to_metrics(best['nums'])
        sub_results[sub] = {'req': req, 'got': met['合計'] or 0}

    # build results dict (取得値を取り出しやすく)
    parsed = {}
    for key, req in GRAD_REQUIREMENTS.items():
        sel = main_selected.get(key)
        got = sel['metrics']['合計'] if (sel and sel['metrics']['合計'] is not None) else 0
        parsed[key] = got
    # add sub items (overwrite if present)
    for sub, info in sub_results.items():
        parsed[sub] = info['got']

    # -------------------------
    # 不足計算（カテゴリごと・サブごと・自由履修を推定）
    # -------------------------
    lack_dict = {}
    known_shortage_sum = 0

    # main categories (除: 合計)
    for key, req in GRAD_REQUIREMENTS.items():
        if key == "合計":
            continue
        got = int(parsed.get(key, 0) or 0)
        if got < req:
            lack = req - got
            lack_dict[key] = lack
            known_shortage_sum += lack

    # sub requirements
    for sub, req in SUB_REQUIREMENTS.items():
        got = int(parsed.get(sub, 0) or 0)
        if got < req:
            lack = req - got
            lack_dict[sub] = lack
            known_shortage_sum += lack

    # total missing (合計)
    total_req = GRAD_REQUIREMENTS.get("合計", 0)
    total_got = int(parsed.get("合計", 0) or 0)
    total_missing = max(0, total_req - total_got)
    lack_dict["合計"] = total_missing

    # 自由履修の不足を推測（合計 - 既知の不足）
    free_missing = total_missing - known_shortage_sum
    if free_missing > 0:
        lack_dict["自由履修科目"] = free_missing

    # -------------------------
    # フォーマット済みテキストを作成（ユーザー向け出力）
    # -------------------------
    lines_out = []
    lines_out.append("=== 各カテゴリチェック ===")
    for key, req in GRAD_REQUIREMENTS.items():
        if key == "合計":
            continue
        got = parsed.get(key, 0)
        if got is None:
            got_display = "―"
            status = "❌ データなし"
        else:
            got_display = str(got)
            if got < req:
                status = f"❌ 不足 {req - got}"
            else:
                if key == "外国語科目区分":
                    sub_ng = False
                    for subk in ("英語（初級）", "初習外国語"):
                        if parsed.get(subk, 0) < SUB_REQUIREMENTS.get(subk, 0):
                            sub_ng = True
                    status = "🔺 備考不足あり" if sub_ng else "✅"
                else:
                    status = "✅"
        lines_out.append(f"・{key:<20} 必要={req:<3}  取得={got_display:<3}  {status}")

    # 合計行
    total_got_display = str(total_got) if total_got is not None else "―"
    if total_got < total_req:
        total_status = f"❌ 不足 {total_req - total_got}"
    else:
        total_status = "✅"
    lines_out.append(f"\n合計{'':<15} 必要={total_req:<3}  取得={total_got_display:<3}  {total_status}")

    # 備考（必修科目）
    lines_out.append("\n=== 備考（必修科目） ===")
    for sub, info in SUB_REQUIREMENTS.items():
        need = info
        got = int(parsed.get(sub, 0) or 0)
        if got >= need:
            st = "✅"
        else:
            st = f"❌ 不足 {need - got}"
        lines_out.append(f"{sub:<15} 必要={need:<3}  取得={got:<3}  {st}")

    # 不足一覧（詳細）
    lines_out.append("\n=== 不足している科目区分 ===")
    for k in GRAD_REQUIREMENTS.keys():
        if k == "合計":
            continue
        if k in lack_dict:
            lines_out.append(f"・{k}: あと {lack_dict[k]} 単位")
    for subk in SUB_REQUIREMENTS.keys():
        if subk in lack_dict:
            lines_out.append(f"・{subk}: あと {lack_dict[subk]} 単位")
    if "自由履修科目" in lack_dict:
        lines_out.append(f"・自由履修科目: あと {lack_dict['自由履修科目']} 単位")
    lines_out.append(f"・合計: あと {lack_dict.get('合計', 0)} 単位")

    # 総合判定
    ok_main = all(parsed.get(k, 0) >= v for k, v in GRAD_REQUIREMENTS.items() if k != "合計")
    ok_subs = all(int(parsed.get(k, 0) or 0) >= v for k, v in SUB_REQUIREMENTS.items())
    if ok_main and ok_subs and total_got >= total_req:
        lines_out.append("\n🎉 卒業要件を満たしています")
    else:
        lines_out.append("\n❌ 卒業要件を満たしていません")

    formatted_text = "\n".join(lines_out)

    if return_dict:
        return formatted_text, lack_dict

    return formatted_text


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "成績.pdf"
    print(check_pdf(path))









   
