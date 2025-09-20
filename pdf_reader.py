# pdf_reader.py
# 成績PDFから単位取得状況を解析して卒業要件をチェックするスクリプト
# 必要: pip install pdfplumber pandas

import pdfplumber
import re
from pathlib import Path

PDF_PATH = "成績.pdf"   # PDFファイルのパス
PAGE_NO = 0
DEBUG = False

# -------------------------
# 卒業要件
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

# 備考の必修チェック対象
SUB_REQUIREMENTS = {
    "英語（初級）": 4,
    "初習外国語": 8,
    "外国語を用いた科目": 4   # 追加
}

YEARS_ORDER = ['25','24','23','22']

# -------------------------
# ヘルパー関数
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
    """キーワードを含む行を抽出"""
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
    pre = [int(x) for x in nums[:-1]]
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
# メイン処理
# -------------------------
def check_pdf(pdf_path, page_no=0):
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_no]
        page_width = page.width
        lines = extract_lines_from_page(page, line_tol=6)

    keywords = list(GRAD_REQUIREMENTS.keys()) + list(SUB_REQUIREMENTS.keys()) + ["合計","総合計"]
    logical = find_keyword_logical_rows(lines, keywords)
    logical_sorted = sorted(logical, key=lambda r: (r['top'], r['first_x']))

    # main
    main_selected = {}
    for key in GRAD_REQUIREMENTS.keys():
        cand = [r for r in logical_sorted if normalize(key) in normalize(r['name'])]
        best=None; best_total=-1
        for c in cand:
            met = parse_nums_to_metrics(c['nums'])
            tot = met['合計'] if met['合計'] is not None else -1
            if tot > best_total:
                best_total=tot; best={'metrics':met}
        main_selected[key]=best

    # subs
    sub_results={}
    for sub, req in SUB_REQUIREMENTS.items():
        candidates=[r for r in logical_sorted if normalize(sub) in normalize(r['name'])]
        if not candidates:
            sub_results[sub]={'req':req,'got':0}
            continue
        best=max(candidates,key=lambda c:int(c['nums'][-1]) if c['nums'] else -1)
        met=parse_nums_to_metrics(best['nums'])
        sub_results[sub]={'req':req,'got':met['合計'] or 0}

    # 出力を文字列でまとめる
    output_lines = []
    output_lines.append("=== 各カテゴリチェック ===")
    for key, req in GRAD_REQUIREMENTS.items():
        sel=main_selected.get(key)
        got=sel['metrics']['合計'] if sel else None

        # デフォルト判定
        if got is None:
            status="❌ データなし"
        elif got<req:
            status=f"❌ 不足 {req-got}"
        else:
            # 合計OKだけど備考不足があるカテゴリは 🔺
            if key=="外国語科目区分":
                if sub_results["英語（初級）"]['got'] < sub_results["英語（初級）"]['req'] \
                   or sub_results["初習外国語"]['got'] < sub_results["初習外国語"]['req']:
                    status="🔺 備考不足あり"
                else:
                    status="✅"
            else:
                status="✅"

        output_lines.append(f"{key:<20} 必要={req:<3}  取得={got:<3}  {status}")

    output_lines.append("\n=== 備考（必修科目） ===")
    for sub,info in sub_results.items():
        need, got = info['req'], info['got']
        if got>=need:
            status="✅"
        else:
            status=f"❌ 不足 {need-got}"
        output_lines.append(f"{sub:<15} 必要={need:<3}  取得={got:<3}  {status}")

    output_lines.append("\n=== 総合判定 ===")
    ok_main = all((sel and sel['metrics']['合計'] is not None and sel['metrics']['合計']>=req)
                  for key,req in GRAD_REQUIREMENTS.items() if key!="合計")
    ok_subs = all(info['got']>=info['req'] for info in sub_results.values())
    total_req=GRAD_REQUIREMENTS['合計']
    total_got=main_selected['合計']['metrics']['合計'] if main_selected['合計'] else None

    if ok_main and ok_subs and total_got>=total_req:
        output_lines.append("🎉 卒業要件を満たしています")
    else:
        output_lines.append("❌ 卒業要件を満たしていません")

    return "\n".join(output_lines)

# デバッグ用
if __name__=="__main__":
    print(check_pdf(PDF_PATH, PAGE_NO))








   
