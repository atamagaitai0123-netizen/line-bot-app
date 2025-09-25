import pandas as pd
from supabase import create_client, Client

# Supabase 接続情報
url = "https://zqihsfkgjaenzndopzpk.supabase.co"
key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpxaWhzZmtnamFlbnpuZG9wenBrIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1ODM1OTAxNSwiZXhwIjoyMDczOTM1MDE1fQ.kNuU6cQ5JpWhKHejYL-uFxZVuAExY9vH0pBGqBcIEUo"

supabase: Client = create_client(url, key)

# CSV を読み込み
df = pd.read_csv("syllabus_parsed.csv")

for _, row in df.iterrows():
    data = {
        "subject_teacher": str(row["授業名・教員"]),
        "units": str(row["単位"]),
        "grade_year": str(row["年次"]),
        "semester": str(row["学期"]),
        "campus": str(row["キャンパス"]),
        "evaluation": str(row["成績評価"])
    }

    supabase.table("syllabus").insert(data).execute()

print("✅ Supabase にデータをアップロードしました！")
