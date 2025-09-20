import pdfplumber
import re
import statistics
from pathlib import Path
from typing import List, Dict, Any, Optional

# === 定数・設定 ===
# 君の大学の正式な要件に合わせて変更してね
REQUIREMENTS = {
    "学部必修科目区分": 12,
    "教養科目区分": 24,
    "外国語科目区分": 16,
    "体育実技科目区分": 2,
    "経営学科基礎専門科目": 14,
    "経営学科専門科目": 32,
    "自由履修科目": 24,
    "合計": 124,
}

# 備考欄の必修内訳の要件
REMARK_REQUIREMENTS = {
    "英語（初級）": 4,
    "初習外国語": 8,
    "外国語を用いた科目": 4,
}

# 自由履修科目に含まれるカテゴリ
# PDFの表に記載されているカテゴリを正確に定義
FREE_ELECTIVE_CATEGORIES = [
    "他学科専門科目",
    "経営学科教職専門科目",
    "実習関連科目",
    "ICTリテラシー科目",
    "演習科目（演習Ⅰ）",
    "演習科目（演習ⅡA~ⅡIB）",
    "全学共通総合講座",
    "国際教育プログラム科目",
    "グローバル人材育成プログラム科目",
    "他学部科目",
]

# === ヘルパー関数 ===
def normalize(s: str) -> str:
    """文字列の正規化（全角半角、スペースを統一）"""
    return re.sub(r"\s+", "", s).lower() if s else ""

def is_year_token(n: str) -> bool:
    """年度を識別するヘルパー関数"""
    return n.isdigit() and 22 <= int(n) <= 25

def extract_rows_from_page(page, line_tolerance: float = 5.0) -> List[Dict[str, Any]]:
    """
    ページから行単位でテキストと単語の位置情報を抽出する
    """
    words = page.extract_words()
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: (w['top'], w['x0']))
    rows = []
    current_row = []
    
    if not words_sorted:
        return rows
        
    current_top = words_sorted[0]['top']
    for w in words_sorted:
        if abs(w['top'] - current_top) <= line_tolerance:
            current_row.append(w)
        else:
            rows.append(current_row)
            current_row = [w]
            current_top = w['top']
    if current_row:
        rows.append(current_row)
    
    formatted_rows = []
    for row_words in rows:
        sorted_words = sorted(row_words, key=lambda w: w['x0'])
        text = " ".join(w['text'] for w in sorted_words)
        numbers = [int(n) for n in re.findall(r"\d+", text) if not is_year_token(n)]
        
        formatted_rows.append({
            'text': text,
            'numbers': numbers,
            'top': sorted_words[0]['top'] if sorted_words else None,
            'first_x': sorted_words[0]['x0'] if sorted_words else None
        })
    return formatted_rows

def find_row_by_keyword(rows: List[Dict[str, Any]], keyword: str) -> Optional[Dict[str, Any]]:
    """キーワードで特定の行を探す"""
    for row in rows:
        if normalize(keyword) in normalize(row.get('text', '')):
            return row
    return None

def analyze_grades(pdf_path: str):
    """
    PDFを解析し、単位の不足状況を計算するメイン関数。
    """
    try:
        if not Path(pdf_path).exists():
            print(f"❌ エラー: '{pdf_path}' が見つかりません。ファイル名を確認してください。")
            return

        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            rows = extract_rows_from_page(page)

            # 単位取得状況の行を解析
            extracted_units = {}
            for row in rows:
                row_text = normalize(row['text'])
                row_numbers = row['numbers']
                
                # 正規のカテゴリを抽出
                for category in REQUIREMENTS.keys():
                    if normalize(category) in row_text:
                        if len(row_numbers) > 0:
                            extracted_units[category] = row_numbers[-1]
                
                # 備考欄の内訳を抽出
                for subject in REMARK_REQUIREMENTS.keys():
                    if normalize(subject) in row_text:
                        if len(row_numbers) > 0:
                            extracted_units[subject] = row_numbers[-1]
                
                # 自由履修科目を計算
                if any(normalize(c) in row_text for c in FREE_ELECTIVE_CATEGORIES):
                    if "自由履修科目" not in extracted_units:
                        extracted_units["自由履修科目"] = 0
                    if len(row_numbers) > 0:
                        extracted_units["自由履修科目"] += row_numbers[-1]
                        
            # 合計単位も抽出
            total_row = find_row_by_keyword(rows, "総合計")
            if total_row and total_row['numbers']:
                extracted_units["合計"] = total_row['numbers'][-1]
            
            # 結果表示
            print("✅ 抽出した単位データ:")
            print(extracted_units)

            # 卒業要件と取得単位を比較し、不足単位を計算
            missing_units = {}
            
            # 主要な要件をチェック
            for category, required_units in REQUIREMENTS.items():
                acquired_units = extracted_units.get(category, 0)
                if acquired_units < required_units:
                    missing_units[category] = required_units - acquired_units
            
            # 備考欄の不足もチェック
            for subject, required_units in REMARK_REQUIREMENTS.items():
                acquired_units = extracted_units.get(subject, 0)
                if acquired_units < required_units:
                    missing_units[subject] = required_units - acquired_units

            # 結果を表示
            print("\n-------------------------------")
            if not missing_units:
                print("🎉 卒業要件の単位はすべて満たされています！")
            else:
                print("⚠️ 卒業に必要な単位が不足しています:")
                for category, units in missing_units.items():
                    print(f"- {category}: あと {units} 単位必要です。")
            print("-------------------------------")

    except Exception as e:
        print(f"❌ 予期せぬエラーが発生しました: {e}")

# 関数の実行
analyze_grades(pdf_path="成績.pdf")
