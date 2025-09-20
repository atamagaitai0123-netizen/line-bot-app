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

YEARS_ORDER = ['25','24','23','22']

# 自由履修科目に含めるカテゴリ
FREE_ELECTIVE_SOURCES = [
    "実習関連科目",
    "ICTリテラシー科目",
    "演習科目(演習I)",
    "演習科目(演習IIA~IIIB)",
    "全学共通総合講座",
    "国際教育プログラム科目",
    "グローバル人材育成プログラム科目",
    "他学部科目"
]

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

def extract_credits(text: str):
    """
    「◯単位」の直前の数字を優先して抽出。
    年度（20xxなど）は無視。
    """
    matches = re.findall(r'(\d+)\s*単位', text)
    if matches:
        return int(matches[-1])
    return None

def find_keyword_logical_rows(lines, keywords):
    """キーワードを含む行を抽出"""
    logical = []
    for ln in lines:
        text = ln['text']
        for kw in keywords:
            if normalize(kw) in normalize(text):
                credits = extract_credits(text)
                nums = re.findall(r'\d+', text)
                logical.append({
                    'name': text,
                    'first_x': ln['first_x'],
                    'top': ln['top'],
                    'nums': nums,
                    'credits': credits
                })
                break
    return logical

def parse_nums_to_metrics(row):
    """行から必要・年度別・合計を抽出"""
    if row['credits'] is not None:
        total = row['credits']
    else:
        nums = [int(x) for x in row['nums'] if int(x) < 2000]  # 年度を除外
        total = nums[-1] if nums else None
    return {'必要': None, 'years': {}, '合計': total}

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

    keywords = list(GRAD_REQUIREMENTS.keys()) + list(SUB_REQUIREMENTS.keys()) + FREE_ELECTIVE_SOURCES + ["合計","総合計"]
    logical = find_keyword_logical_rows(lines, keywords)

    # main
    main_selected = {}
    for key in GRAD_REQUIREMENTS.keys():
        cand = [r for r in logical if normalize(key) in normalize(r['name'])]
        best=None
        if cand:
            best=cand[0]
        main_selected[key]={'metrics':parse_nums_to_metrics(best)} if best else None

    # 自由履修 = 複数カテゴリの合算
    free_total = 0
    for src in FREE_ELECTIVE_SOURCES:
        cand=[r for r in logical if normalize(src) in normalize(r['name'])]
        if cand and cand[0]['credits'] is not None:
            free_total += cand[0]['credits']
    main_selected["自由履修科目"]={'metrics':{'必要':GRAD_REQUIREMENTS["自由履修科目"],'years':{},'合計':free_total}}

    # subs
    sub_results={}
    for sub, req in SUB_REQUIREMENTS.items():
        candidates=[r for r in logical if normalize(sub) in normalize(r['name'])]
        if not candidates:
            sub_results[sub]={'req':req,'got':0}
            continue
        got=candidates[0]['credits'] or 0
        sub_results[sub]={'req':req,'got':got}

    # 出力組み立て
    result_lines=[]
    result_lines.append("成績表を解析しました！\n")
    result_lines.append("=== 各カテゴリチェック ===")
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
        result_lines.append(f"・{key:<20} 必要={req:<3}  取得={got if got is not None else '―':<3}  {status}")

    result_lines.append("\n=== 備考（必修科目） ===")
    for sub,info in sub_results.items():
        need, got = info['req'], info['got']
        status="✅" if got>=need else f"❌ 不足 {need-got}"
        result_lines.append(f"{sub:<15} 必要={need:<3}  取得={got:<3}  {status}")

    # 不足一覧
    result_lines.append("\n=== 不足している科目区分 ===")
    total_req=GRAD_REQUIREMENTS['合計']
    total_got=sum(sel['metrics']['合計'] for sel in main_selected.values() if sel and sel['metrics']['合計'] is not None)
    deficits=[]
    for key, req in GRAD_REQUIREMENTS.items():
        if key=="合計": continue
        got=main_selected[key]['metrics']['合計'] if main_selected[key] else 0
        if got<req:
            deficits.append(f"・{key}: あと {req-got} 単位")
    for sub,info in sub_results.items():
        if info['got']<info['req']:
            deficits.append(f"・{sub}: あと {info['req']-info['got']} 単位")
    # 合計不足
    total_deficit=total_req-total_got
    if total_deficit>0:
        deficits.append(f"・合計: あと {total_deficit} 単位")
    result_lines.extend(deficits)

    if total_deficit==0 and all(info['got']>=info['req'] for info in sub_results.values()):
        result_lines.append("\n🎉 卒業要件を満たしています")
    else:
        result_lines.append("\n❌ 卒業要件を満たしていません")

    result="\n".join(result_lines)
    if return_dict:
        return {"text":result,"main":main_selected,"subs":sub_results}
    return result

if __name__=="__main__":
    print(check_pdf(PDF_PATH, PAGE_NO))






   
