import csv
import pdfplumber
import re

pdf_path = "20250401_sy2025.pdf"

with pdfplumber.open(pdf_path) as pdf:
    rows = []
    for page_num in range(22, 392):  # ページ範囲は必要に応じて調整
        page = pdf.pages[page_num]

        width = page.width
        height = page.height

        # 左右に分割
        left = page.crop((0, 0, width/2, height))
        right = page.crop((width/2, 0, width, height))

        for part, name in [(left, "LEFT"), (right, "RIGHT")]:
            text = part.extract_text() or ""

            # 1️⃣ 科目ナンバー～1.授業の概要・到達目標（全角/半角対応）
            match1 = re.search(r"科目ナンバー[\s\S]*?(?=[0-9１]．?授業の概要・到達目標)", text)
            info_block = match1.group(0).strip() if match1 else "該当なし"

            # 2️⃣ 8.成績評価の方法～9.その他（全角/半角対応）
            match2 = re.search(r"[8８]．?成績評価の方法[\s\S]*?(?=[9９]．?その他)", text)
            grade_block = match2.group(0).strip() if match2 else "該当なし"

            print(f"\n=== Page {page_num+1} {name} ===")
            print("科目情報:\n", info_block)
            print("成績評価:\n", grade_block)

            # --- 科目情報をカラム分け ---
            subject_number = ""
            subject_name = ""
            teachers = ""
            credits = ""
            semester = ""
            campus = ""

            if info_block != "該当なし":
                # 科目ナンバー
                num_match = re.search(r"科目ナンバー[:：]?\s*([^\s]+)", info_block)
                if num_match:
                    subject_number = num_match.group(1)

                # 単位
                unit_match = re.search(r"([0-9０-９]+)\s*単位", info_block)
                if unit_match:
                    credits = unit_match.group(1) + "単位"

                # 学期
                sem_match = re.search(r"(春学期|秋学期)", info_block)
                if sem_match:
                    semester = sem_match.group(1)

                # キャンパス/オンライン
                camp_match = re.search(r"(和泉キャンパス|駿河台キャンパス|中野キャンパス|オンライン)", info_block)
                if camp_match:
                    campus = camp_match.group(1)

                # 教員名（「単位」の前にある行をざっくり抽出）
                teacher_match = re.search(r"\n([^\n]*?)(?:\s*[0-9０-９]+\s*単位)", info_block)
                if teacher_match:
                    teachers = teacher_match.group(1).strip()

                # 授業名（科目ナンバーの次の行）
                lines = info_block.splitlines()
                if len(lines) > 1:
                    subject_name = lines[1].strip()

            # CSV用に保存
            rows.append([
                f"Page {page_num+1} {name}",
                subject_number,
                subject_name,
                teachers,
                credits,
                semester,
                campus,
                grade_block.replace("\n", " ")
            ])

# CSV保存
with open("syllabus.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow([
        "ページ",
        "科目ナンバー",
        "授業名",
        "教員",
        "単位",
        "学期",
        "キャンパス/オンライン",
        "成績評価"
    ])
    writer.writerows(rows)
