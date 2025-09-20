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
    "外国語を用いた科目": 4
}

# 自由履修を構成する科目群
FREE_KEYS = [
    "実習関連科目",
    "ICTリテラシー科目",
    "演習科目(演習I)",
    "演習科目(演習IIA~IIIB)",
    "全学共通総合講座",
    "国際教育プログラム科目",
    "グローバル人材育成プログラム科目",
    "他学部科目"
]

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
def check_pdf(pdf_path, page_no=0, return_dict=False):
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_no]
        lines = extract_lines_from_page(page, line_tol=6)

    # キーワード探し
    keywords = list(GRAD_REQUIREMENTS.keys()) + list(SUB_REQUIREMENTS.keys()) + FREE_KEYS + ["合計","総合計"]
    logical = []
    for ln in lines:
        text = ln['text']
        for kw in keywords:
            if normalize(kw) in normalize(text):
                logical.append({'name': kw, 'text': text, 'nums': ln['nums'], 'top': ln['top']})
    logical_sorted = sorted(logical, key=lambda r: (r['top']))

    # mainカテゴリ抽出
    main_selected = {}
    for key in GRAD_REQUIREMENTS.keys():
        if key in ["合計","総合計"]:
            # 特別扱い：合計は最後の数値
            cand = [r for r in logical_sorted if normalize(key) in normalize(r['name']) or normalize(key) in normalize(r['text'])]
        else:
            cand = [r for r in logical_sorted if normalize(key) in normalize(r['name'])]
        if not cand: continue
        best = max(cand, key=lambda c: int(c['nums'][-1]) if c['nums'] else -1)
        met = parse_nums_to_metrics(best['nums'])
        main_selected[key] = {'metrics': met}

    # 自由履修 = FREE_KEYS合算
    free_total = 0
    for fk in FREE_KEYS:
        cands = [r for r in logical_sorted if normalize(fk) in normalize(r['name']) or normalize(fk) in normalize(r['text'])]
        if cands:
            best = max(cands, key=lambda c: int(c['nums'][-1]) if c['nums'] else 0)
            if best['nums']:
                free_total += int(best['nums'][-1])
    main_selected["自由履修科目"] = {'metrics': {'必要': GRAD_REQUIREMENTS["自由履修科目"], 'years': {}, '合計': free_total}}

    # subs（備考欄専用）
    sub_results={}
    for sub, req in SUB_REQUIREMENTS.items():
        cands=[r for r in logical_sorted if normalize(sub)==normalize(r['name'])]
        if not cands:
            sub_results[sub]={'req':req,'got':0}
            continue
        best = max(cands, key=lambda c:int(c['nums'][-1]) if c['nums'] else -1)
        met = parse_nums_to_metrics(best['nums'])
        sub_results[sub]={'req':req,'got':met['合計'] or 0}

    # 出力用
    output = []
    output.append("成績表を解析しました！\n")
    output.append("=== 各カテゴリチェック ===")
    for key, req in GRAD_REQUIREMENTS.items():
        sel=main_selected.get(key)
        got=sel['metrics']['合計'] if sel else None

        if got is None:
            status="❌ データなし"
        elif got<req:
            status=f"❌ 不足 {req-got}"
        else:
            if key=="外国語科目区分":
                if sub_results["英語（初級）"]['got'] < sub_results["英語（初級）"]['req'] \
                   or sub_results["初習外国語"]['got'] < sub_results["初習外国語"]['req']:
                    status="🔺 備考不足あり"
                else:
                    status="✅"
            else:
                status="✅"
        output.append(f"・{key:<20} 必要={req:<3}  取得={got if got is not None else '―':<3}  {status}")

    output.append("\n=== 備考（必修科目） ===")
    for sub,info in sub_results.items():
        need, got = info['req'], info['got']
        status="✅" if got>=need else f"❌ 不足 {need-got}"
        output.append(f"{sub:<15} 必要={need:<3}  取得={got:<3}  {status}")

    # 不足科目算出
    output.append("\n=== 不足している科目区分 ===")
    lacking=[]
    total_req=GRAD_REQUIREMENTS['合計']
    total_got=main_selected['合計']['metrics']['合計'] if '合計' in main_selected else 0
    sum_lacking_known=0
    for key, req in GRAD_REQUIREMENTS.items():
        if key=="合計": continue
        got = main_selected.get(key,{}).get('metrics',{}).get('合計',0)
        if got<req:
            lacking.append(f"・{key}: あと {req-got} 単位")
            sum_lacking_known += (req-got)
    # 自由履修補正
    free_lack = (total_req-total_got) - sum_lacking_known
    if free_lack>0:
        lacking.append(f"・自由履修科目: あと {free_lack} 単位")
    lacking.append(f"・合計: あと {total_req-total_got} 単位")
    output.extend(lacking)

    if total_got>=total_req and all(v['got']>=v['req'] for v in sub_results.values()):
        output.append("\n🎉 卒業要件を満たしています")
    else:
        output.append("\n❌ 卒業要件を満たしていません")

    result_text="\n".join(output)
    if return_dict:
        return {"text": result_text, "main": main_selected, "subs": sub_results}
    return result_text


if __name__=="__main__":
    print(check_pdf(PDF_PATH, PAGE_NO))
