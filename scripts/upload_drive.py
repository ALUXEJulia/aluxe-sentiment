#!/usr/bin/env python3
"""
ALUXE 競品週報 Google Drive 上傳模組

把產出的 PDF 上傳到指定 Google Drive 資料夾，回傳可分享的網址。

使用方式（CLI）：
    python scripts/upload_drive.py <pdf_path> [<folder_id>]

當模組呼叫：
    from scripts.upload_drive import upload_pdf_to_drive
    url = upload_pdf_to_drive(pdf_path, folder_id, service_account_dict)

這個模組刻意不重複實作 Google JWT 簽章 — 它呼叫者要先把
SERVICE_ACCOUNT 字典 + GOOGLE_DRIVE_FOLDER_ID 提供進來。

驗證範圍：
  - https://www.googleapis.com/auth/drive.file
    （只能讀寫「自己上傳的檔案」，比 drive 全權限安全）
"""
import json
import os
import sys
import base64
import time
from pathlib import Path

import requests

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink,name"
PERMISSIONS_URL = "https://www.googleapis.com/drive/v3/files/{file_id}/permissions"


def _b64url(s: bytes) -> str:
    return base64.urlsafe_b64encode(s).rstrip(b"=").decode()


def _drive_token(service_account: dict) -> str:
    """模仿主腳本 google_token() 的做法，但 scope 換成 Drive"""
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claim = _b64url(json.dumps({
        "iss": service_account["client_email"],
        "scope": DRIVE_SCOPE,
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600,
        "iat": now,
    }).encode())

    key = serialization.load_pem_private_key(
        service_account["private_key"].encode(),
        password=None,
        backend=default_backend(),
    )
    sig = _b64url(key.sign(f"{header}.{claim}".encode(), padding.PKCS1v15(), hashes.SHA256()))

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": f"{header}.{claim}.{sig}",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def upload_pdf_to_drive(
    pdf_path: Path,
    folder_id: str,
    service_account: dict,
    *,
    make_link_viewable: bool = False,
    rename: str = None,
) -> dict:
    """
    上傳 PDF 到指定 Drive 資料夾。

    Args:
        pdf_path: 本地 PDF 檔案路徑
        folder_id: Google Drive 資料夾 ID
        service_account: 已解析過的 service account JSON 字典
        make_link_viewable: True = 設「任何有連結的人可以看」(reader)
                            False = 只有資料夾權限的人能看（建議，因為資料夾已限制 @aluxe.com / @joycolori.com）
        rename: 上傳後的檔名（不給就用本地檔案名）

    Returns:
        dict: {"id": "xxx", "webViewLink": "https://...", "name": "xxx.pdf"}
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"找不到 PDF：{pdf_path}")
    if not folder_id:
        raise ValueError("folder_id 不可為空（GitHub Secret: GOOGLE_DRIVE_FOLDER_ID）")

    print(f"[Drive] 上傳 {pdf_path.name} 到資料夾 {folder_id[:12]}...")
    token = _drive_token(service_account)

    file_name = rename or pdf_path.name
    metadata = {
        "name": file_name,
        "parents": [folder_id],
        "mimeType": "application/pdf",
    }

    # multipart upload：metadata + file body
    boundary = "ALUXE_DRIVE_UPLOAD_BOUNDARY"
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + pdf_path.read_bytes() + f"\r\n--{boundary}--".encode()

    resp = requests.post(
        UPLOAD_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        data=body,
        timeout=120,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Drive 上傳失敗 [{resp.status_code}]：{resp.text[:500]}")

    info = resp.json()
    file_id = info["id"]
    print(f"  ✅ 已上傳：{info.get('webViewLink', '')}")

    # 視需要設權限（一般不需要 — 資料夾本身已透過 @aluxe.com / @joycolori.com 控制）
    if make_link_viewable:
        perm_resp = requests.post(
            PERMISSIONS_URL.format(file_id=file_id),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"role": "reader", "type": "anyone"},
            timeout=30,
        )
        if perm_resp.status_code < 300:
            print("  ✅ 已設定任何有連結者可瀏覽")
        else:
            print(f"  ⚠️ 權限設定失敗（不影響上傳）：{perm_resp.text[:200]}")

    return info


def main():
    if len(sys.argv) < 2:
        print("使用方式：python scripts/upload_drive.py <pdf_path> [<folder_id>]")
        sys.exit(1)
    pdf_path = Path(sys.argv[1])
    folder_id = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
    service_account = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
    info = upload_pdf_to_drive(pdf_path, folder_id, service_account)
    print(json.dumps(info, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
