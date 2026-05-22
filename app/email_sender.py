"""寄 email 驗證碼（SendGrid）

環境變數：
- SENDGRID_API_KEY：SendGrid API key（沒設 → dev mode、print code 到 console）
- SENDGRID_FROM_EMAIL：寄信來源 email（已在 SendGrid 後台驗證過的）

dev mode：API_KEY 沒設時、不真寄信、把 code 印到 stdout 並回 True、保留 caller 流程不變
"""
import os


def _is_configured() -> bool:
    return bool(os.getenv("SENDGRID_API_KEY") and os.getenv("SENDGRID_FROM_EMAIL"))


def send_verification_code(to_email: str, code: str) -> bool:
    """寄 6 位 email 驗證碼到 to_email
    回 True 表示成功（或 dev mode print 成功）、False 表示寄失敗（API error）
    """
    if not _is_configured():
        print(f"[EMAIL/DEV] to={to_email} code={code} (SendGrid 未設定、走 dev mode)")
        return True

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
    except ImportError:
        print("[EMAIL/ERR] sendgrid 套件未安裝、退回 dev mode")
        print(f"[EMAIL/DEV] to={to_email} code={code}")
        return True

    subject = "卡波 - 您的註冊驗證碼"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;background:#f7f8fa;">
      <h2 style="color:#1a1a1a;">卡波 註冊驗證碼</h2>
      <p style="color:#555;">您正在註冊卡波（寶可夢卡 PSA 鑑定價格查詢）。驗證碼如下：</p>
      <div style="font-size:32px;font-weight:bold;color:#e63946;letter-spacing:6px;text-align:center;padding:24px;background:#fff;border-radius:8px;margin:16px 0;">
        {code}
      </div>
      <p style="color:#888;font-size:13px;">10 分鐘內有效。若非本人操作、請忽略此信。</p>
    </div>
    """
    message = Mail(
        from_email=os.environ["SENDGRID_FROM_EMAIL"],
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )
    try:
        sg = SendGridAPIClient(os.environ["SENDGRID_API_KEY"])
        resp = sg.send(message)
        ok = 200 <= resp.status_code < 300
        if not ok:
            print(f"[EMAIL/ERR] to={to_email} status={resp.status_code} body={resp.body}")
        return ok
    except Exception as e:
        print(f"[EMAIL/ERR] to={to_email} exception={e}")
        return False
