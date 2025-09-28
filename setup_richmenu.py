import requests

# LINE チャネルアクセストークン（必ず "" で囲む）
CHANNEL_ACCESS_TOKEN = "vCxmZcgdsuKSL1dQ/Oe7UtR4YF/WhGuOSrwOaZLTty47r4xNU/wx+wo9OjEd3FIzngetp/N3WxEDQOeZmrjWQcUY0BRfxqkUG/BMNJ3EykHYQNUtikbq2mbZ/NyCPV74sDzvL65qS9iTdqPpFCxZDQdB04t89/1O/w1cDnyilFU="
API_BASE = "https://api.line.me/v2/bot/richmenu"


# ① リッチメニュー作成（必要なら実行）
def create_richmenu():
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    body = {
        "size": {"width": 2500, "height": 1686},
        "selected": True,
        "name": "CampusNavigatorGuideMenu",
        "chatBarText": "メニューを開く",
        "areas": [
            {
                "bounds": {"x": 0, "y": 0, "width": 2500, "height": 843},
                "action": {"type": "message", "text": "使い方ガイド"}
            },
            {
                "bounds": {"x": 0, "y": 843, "width": 2500, "height": 843},
                "action": {"type": "message", "text": "年間行事予定"}
            }
        ]
    }
    res = requests.post(API_BASE, headers=headers, json=body)
    print("Create:", res.status_code, res.text)
    return res.json()


# ② 作成済みリッチメニューに画像をアップロード
def upload_richmenu_image(richmenu_id, image_path="richmenu_resized.jpg"):
    headers = {"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}
    with open(image_path, "rb") as f:
        res = requests.post(
            f"{API_BASE}/{richmenu_id}/content",
            headers=headers,
            data=f,   # ←ここは files ではなく data=f でもOK
            # files={"file": f} でも可
        )
    print("Upload status:", res.status_code, res.text)


# ③ 全ユーザーにリッチメニューをリンク
def link_richmenu_to_all_users(richmenu_id):
    headers = {"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}
    res = requests.post(
        f"https://api.line.me/v2/bot/user/all/richmenu/{richmenu_id}",
        headers=headers
    )
    print("Link status:", res.status_code, res.text)


if __name__ == "__main__":
    # ② 画像をアップロード
    upload_richmenu_image(
        "richmenu-3ff4bc614eb6cfabac5f108eeac94183",  # ← あなたの richMenuId
        "/Users/ichikawashinnosuke/Desktop/line-bot-app/richmenu_resized.jpg"
    )

    # ③ 全ユーザーにリンク（必要ならコメント解除）
    # link_richmenu_to_all_users("richmenu-3ff4bc614eb6cfabac5f108eeac94183")
