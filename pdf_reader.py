# pdf_reader.py
# PDF から単位を抽出し、カテゴリ別の取得単位・不足単位・自由履修の推定まで行う
# 互換性: check_pdf(pdf_path, page_no=0, return_dict=False)

import pdfplumber
import re
from pathlib import Path

# ---------- 要件定義（必要に応じて編集） ----------
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

SUB_REQUIREMENTS = {
    "英語（初級）": 4,
    "初習外国語": 8,
    "外国語を用いた科目": 4
}

# 自由履修に含めるソースカテゴリ（成績表の表記そのままを並べる）
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

YEARS_ORDER = ['25','24','23','22']

# ----------------- ヘルパー -----------------
def normalize(s: str) -> str:
    return "" if s is None else re.sub(r'\s+', '', str(s))

def extract_lines_from_page(page, line_tol=6):
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
    # 重複削除
    uniq = []
    seen = set()
    for r in logical:
        key = (r['top'], r['first_x'], r['name'])
        if key not in seen:
            seen.add(key); uniq.append(r)
    return uniq

def parse_nums_to_metrics(nums):
    if not nums:
        return {'必要': None, 'years': {}, '合計': None}
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

# ----------------- メイン関数 -----------------
def check_pdf(pdf_path, page_no=0, return_dict=False):
    """
    互換:
      - check_pdf(path) -> formatted_text (string)
      - check_pdf(path, return_dict=True) -> (formatted_text, lack_dict)
      - check_pdf(path, page_no=0, return_dict=True) works too
    """
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        # page_no が範囲外なら最後のページを使う
        if page_no < 0 or page_no >= len(pdf.pages):
            page = pdf.pages[-1]
        else:
            page = pdf.pages[page_no]
        lines = extract_lines_from_page(page, line_tol=6)

    keywords = list(GRAD_REQUIREMENTS.keys()) + list(SUB_REQUIREMENTS.keys()) + FREE_ELECTIVE_SOURCES + ["合計","総合計"]
    logical = find_keyword_logical_rows(lines, keywords)
    logical_sorted = sorted(logical, key=lambda r: (r['top'], r['first_x']))

    # main 選定（各カテゴリの合計を探す）
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

    # subs（備考の必修等）
    sub_results = {}
    for sub, req in SUB_REQUIREMENTS.items():
        candidates = [r for r in logical_sorted if normalize(sub) in normalize(r['name'])]
        if not candidates:
            sub_results[sub] = {'req': req, 'got': 0}
            continue
        best = max(candidates, key=lambda c: int(c['nums'][-1]) if c['nums'] else -1)
        met = parse_nums_to_metrics(best['nums'])
        sub_results[sub] = {'req': req, 'got': met['合計'] or 0}

    # parsed dict: 各カテゴリの取得単位を取り出す（存在しないものは0）
    parsed = {}
    for key in GRAD_REQUIREMENTS.keys():
        sel = main_selected.get(key)
        got = int(sel['metrics']['合計']) if (sel and sel['metrics']['合計'] is not None) else 0
        parsed[key] = got
    # sub を上書き（備考に取れている場合）
    for sub, info in sub_results.items():
        parsed[sub] = int(info['got'] or 0)

    # --- 自由履修の元になるカテゴリの合算を反映 ---
    free_sum = 0
    for src in FREE_ELECTIVE_SOURCES:
        # まず、parsed にキーそのままで存在するか試す
        if src in parsed:
            free_sum += int(parsed.get(src, 0) or 0)
            continue
        # 別に検出できる行があるかチェック（ログical から）
        for r in logical_sorted:
            if normalize(src) in normalize(r['name']):
                # r['nums'] の末尾を合計と仮定
                if r['nums']:
                    try:
                        free_sum += int(r['nums'][-1])
                    except Exception:
                        pass
                break
    # 優先して、もし既に main に「自由履修科目」取得が入っていればそちらを使う（成績表に直接載っている場合）
    if parsed.get("自由履修科目", 0) > 0:
        # 信頼できる既存値を優先（ただし free_sum をマージしても良いが二重化に注意）
        parsed["自由履修科目"] = int(parsed["自由履修科目"])
    else:
        parsed["自由履修科目"] = int(free_sum or 0)

    # 合計は parsed にない場合はテーブルの合計行を使う（main_selected から）
    total_got = int(parsed.get("合計", 0) or 0)
    if total_got == 0:
        sel_total = main_selected.get("合計")
        if sel_total and sel_total['metrics']['合計'] is not None:
            total_got = int(sel_total['metrics']['合計'])

    # -------------------------
    # 不足計算（自由履修は合計差分から推定）
    # -------------------------
    lack_dict = {}
    known_short_sum = 0

    # main categories (自由履修と合計は除外して既知不足を計上)
    for key, req in GRAD_REQUIREMENTS.items():
        if key in ("合計", "自由履修科目"):
            continue
        got = int(parsed.get(key, 0) or 0)
        if got < req:
            lack = req - got
            lack_dict[key] = lack
            known_short_sum += lack

    # sub requirements（備考内の必修）
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

    # 自由履修の不足を推定（合計不足 - 既知不足）
    free_missing = total_missing - known_short_sum
    if free_missing > 0:
        # ただし既に parsed['自由履修科目']（取得）がある場合は、それを使って不足を計算する
        free_have = int(parsed.get("自由履修科目", 0) or 0)
        free_need = GRAD_REQUIREMENTS.get("自由履修科目", 0)
        # 自由履修の不足は max(推定, 必要-取得)
        deduced_free_missing = max(0, free_need - free_have)
        # 優先的に（推定 と deduced のどちらが大きいか）を採用して表示に使う
        chosen_free_missing = max(free_missing, deduced_free_missing)
        # ただし chosen_free_missing を lack_dict に入れる前に、二重計上を避けるため既存に追加しないで上書き
        lack_dict["自由履修科目"] = chosen_free_missing

    # -------------------------
    # フォーマット済みテキスト生成
    # -------------------------
    lines_out = []
    lines_out.append("=== 各カテゴリチェック ===")
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
                    sub_ng = False
                    for subk in ("英語（初級）", "初習外国語"):
                        if int(parsed.get(subk, 0) or 0) < SUB_REQUIREMENTS.get(subk, 0):
                            sub_ng = True
                    status = "🔺 備考不足あり" if sub_ng else "✅"
                else:
                    status = "✅"
        lines_out.append(f"・{key:<20} 必要={req:<3}  取得={got_display:<3}  {status}")

    # 合計行
    total_got_display = str(total_got) if total_got is not None else "―"
    total_status = (f"❌ 不足 {total_req - total_got}" if total_got < total_req else "✅")
    lines_out.append(f"\n合計{'':<15} 必要={total_req:<3}  取得={total_got_display:<3}  {total_status}")

    # 備考（必修科目）
    lines_out.append("\n=== 備考（必修科目） ===")
    for sub, need in SUB_REQUIREMENTS.items():
        got = int(parsed.get(sub, 0) or 0)
        st = "✅" if got >= need else f"❌ 不足 {need - got}"
        lines_out.append(f"{sub:<15} 必要={need:<3}  取得={got:<3}  {st}")

    # 不足一覧（詳細）
    lines_out.append("\n=== 不足している科目区分 ===")
    # main 欠落（自由履修は除外）
    for k in GRAD_REQUIREMENTS.keys():
        if k in ("合計", "自由履修科目"):
            continue
        if k in lack_dict:
            lines_out.append(f"・{k}: あと {lack_dict[k]} 単位")
    # sub 欠落
    for subk in SUB_REQUIREMENTS.keys():
        if subk in lack_dict:
            lines_out.append(f"・{subk}: あと {lack_dict[subk]} 単位")
    # 自由履修（推定）があれば一度だけ追加
    if "自由履修科目" in lack_dict:
        lines_out.append(f"・自由履修科目: あと {lack_dict['自由履修科目']} 単位")
    # 合計
    lines_out.append(f"・合計: あと {lack_dict.get('合計', 0)} 単位")

    # 総合判定
    ok_main = all(int(parsed.get(k, 0) or 0) >= v for k, v in GRAD_REQUIREMENTS.items() if k != "合計")
    ok_subs = all(int(parsed.get(k, 0) or 0) >= v for k, v in SUB_REQUIREMENTS.items())
    if ok_main and ok_subs and int(total_got or 0) >= total_req:
        lines_out.append("\n🎉 卒業要件を満たしています")
    else:
        lines_out.append("\n❌ 卒業要件を満たしていません")

    formatted_text = "\n".join(lines_out)

    if return_dict:
        return formatted_text, lack_dict

    return formatted_text

# 開発用コマンドライン呼び出し
if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "成績.pdf"
    print(check_pdf(path))






   
