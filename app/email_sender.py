"""寄 email（SendGrid）

環境變數：
- SENDGRID_API_KEY        SendGrid API key（未設 → dev mode，print 到 console）
- SENDGRID_FROM_EMAIL     寄信來源（已在 SendGrid 後台驗證）
- ADMIN_NOTIFY_EMAIL      管理員收信地址（預設 teemo901212@gmail.com）

dev mode：API_KEY 沒設時不真寄信、把內容印到 stdout 並回 True。
"""
import os
from typing import Optional


ADMIN_NOTIFY_EMAIL = os.getenv("ADMIN_NOTIFY_EMAIL", "teemo901212@gmail.com")
SITE_NAME = "卡波 Cardpool"


def _is_configured() -> bool:
    return bool(os.getenv("SENDGRID_API_KEY") and os.getenv("SENDGRID_FROM_EMAIL"))


def _send(to_email: str, subject: str, html: str) -> bool:
    """底層寄信，回 True = 成功（或 dev mode）"""
    if not _is_configured():
        print(f"[EMAIL/DEV] to={to_email}\nsubject={subject}\n---\n{html}\n---")
        return True
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
    except ImportError:
        print("[EMAIL/ERR] sendgrid 套件未安裝，退回 dev mode")
        print(f"[EMAIL/DEV] to={to_email} subject={subject}")
        return True

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
            print(f"[EMAIL/ERR] to={to_email} status={resp.status_code}")
        return ok
    except Exception as e:
        print(f"[EMAIL/ERR] to={to_email} exception={e}")
        return False


# ──────────────────────────────────────────────
#  共用 wrapper style
# ──────────────────────────────────────────────

def _base_html(title: str, body_inner: str) -> str:
    return f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;
            padding:28px 24px;background:#f7f8fa;border-radius:10px;">
  <h2 style="color:#1a1a1a;margin-bottom:4px;">{SITE_NAME}</h2>
  <hr style="border:none;border-top:2px solid #e63946;margin:8px 0 20px;">
  <h3 style="color:#333;">{title}</h3>
  {body_inner}
  <p style="color:#aaa;font-size:12px;margin-top:28px;">
    此信件由系統自動發送，請勿直接回覆。<br>
    © {SITE_NAME}
  </p>
</div>
"""


# ──────────────────────────────────────────────
#  1. 驗證碼（原有）
# ──────────────────────────────────────────────

def send_verification_code(to_email: str, code: str) -> bool:
    """寄 6 位 email 驗證碼"""
    body = f"""
<p style="color:#555;">您正在註冊 {SITE_NAME}。驗證碼如下，10 分鐘內有效：</p>
<div style="font-size:32px;font-weight:bold;color:#e63946;letter-spacing:6px;
            text-align:center;padding:24px;background:#fff;border-radius:8px;margin:16px 0;">
  {code}
</div>
<p style="color:#888;font-size:13px;">若非本人操作，請忽略此信。</p>
"""
    return _send(to_email, f"{SITE_NAME} — 您的驗證碼", _base_html("帳號驗證碼", body))


# ──────────────────────────────────────────────
#  2. 賣家上架成功通知
# ──────────────────────────────────────────────

def send_listing_confirmed(
    to_email: str,
    set_id: str,
    card_number: str,
    grade: int,
    ask_price_twd: float,
    condition: Optional[str] = None,
    listing_id: Optional[int] = None,
) -> bool:
    grade_label = {10: "PSA 10", 9: "PSA 9", 0: "Raw 未鑑定"}.get(grade, str(grade))
    cond_map = {"mint": "全新未拆", "near_mint": "近全新", "excellent": "優良",
                "good": "良好", "poor": "普通"}
    cond_note = f"<br>品相：{cond_map.get(condition, condition)}" if condition else ""
    id_note = f"（掛單 #{listing_id}）" if listing_id else ""

    body = f"""
<p style="color:#555;">您的卡片已成功上架 {id_note}，買家可以看到您的掛單。</p>
<table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fff;
              border-radius:8px;overflow:hidden;">
  <tr><td style="padding:10px 14px;color:#888;width:40%;">系列 / 卡號</td>
      <td style="padding:10px 14px;font-weight:bold;">{set_id} #{card_number}</td></tr>
  <tr style="background:#f7f8fa;"><td style="padding:10px 14px;color:#888;">等級</td>
      <td style="padding:10px 14px;font-weight:bold;">{grade_label}{cond_note}</td></tr>
  <tr><td style="padding:10px 14px;color:#888;">您的定價</td>
      <td style="padding:10px 14px;font-size:20px;font-weight:bold;color:#e63946;">
          NT${ask_price_twd:,.0f}</td></tr>
</table>
<p style="color:#555;">當有買家下單後，我們會再寄信通知您。掛單有效期 30 天。</p>
"""
    return _send(to_email, f"{SITE_NAME} — 卡片上架成功", _base_html("卡片上架成功 ✅", body))


# ──────────────────────────────────────────────
#  3. 買家下單成功通知
# ──────────────────────────────────────────────

def send_order_placed(
    to_email: str,
    set_id: str,
    card_number: str,
    grade: int,
    bid_price_twd: float,
    add_protective_case: bool = False,
    bid_id: Optional[int] = None,
) -> bool:
    grade_label = {10: "PSA 10", 9: "PSA 9", 0: "Raw 未鑑定"}.get(grade, str(grade))
    case_row = ""
    total = bid_price_twd
    if add_protective_case:
        total += 50
        case_row = """
  <tr style="background:#f7f8fa;">
    <td style="padding:10px 14px;color:#888;">加購保護殼</td>
    <td style="padding:10px 14px;">NT$50</td>
  </tr>"""
    id_note = f"（出價 #{bid_id}）" if bid_id else ""

    body = f"""
<p style="color:#555;">您的出價已送出 {id_note}，系統將在有匹配掛單時自動撮合。</p>
<table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fff;
              border-radius:8px;overflow:hidden;">
  <tr><td style="padding:10px 14px;color:#888;width:40%;">系列 / 卡號</td>
      <td style="padding:10px 14px;font-weight:bold;">{set_id} #{card_number}</td></tr>
  <tr style="background:#f7f8fa;"><td style="padding:10px 14px;color:#888;">等級</td>
      <td style="padding:10px 14px;font-weight:bold;">{grade_label}</td></tr>
  <tr><td style="padding:10px 14px;color:#888;">出價金額</td>
      <td style="padding:10px 14px;font-size:20px;font-weight:bold;color:#e63946;">
          NT${bid_price_twd:,.0f}</td></tr>
  {case_row}
</table>
<p style="color:#555;">撮合成功後會再寄一封成交通知給您。出價有效期 30 天。</p>
"""
    subject = f"{SITE_NAME} — 出價送出成功"
    return _send(to_email, subject, _base_html("出價送出成功 ✅", body))


# ──────────────────────────────────────────────
#  4. 成交通知（賣家 + 買家）
# ──────────────────────────────────────────────

def send_trade_matched_buyer(
    to_email: str,
    set_id: str,
    card_number: str,
    grade: int,
    price_twd: float,
    add_protective_case: bool = False,
    trade_id: Optional[int] = None,
) -> bool:
    grade_label = {10: "PSA 10", 9: "PSA 9", 0: "Raw 未鑑定"}.get(grade, str(grade))
    case_row = ""
    total = price_twd
    if add_protective_case:
        total += 50
        case_row = f"""
  <tr style="background:#f7f8fa;">
    <td style="padding:10px 14px;color:#888;">保護殼</td>
    <td style="padding:10px 14px;">NT$50</td>
  </tr>"""
    id_note = f"（成交 #{trade_id}）" if trade_id else ""

    body = f"""
<p style="color:#555;"><strong>恭喜！</strong> 您的出價已成功撮合 {id_note}，請等候賣家出貨。</p>
<table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fff;
              border-radius:8px;overflow:hidden;">
  <tr><td style="padding:10px 14px;color:#888;width:40%;">系列 / 卡號</td>
      <td style="padding:10px 14px;font-weight:bold;">{set_id} #{card_number}</td></tr>
  <tr style="background:#f7f8fa;"><td style="padding:10px 14px;color:#888;">等級</td>
      <td style="padding:10px 14px;font-weight:bold;">{grade_label}</td></tr>
  <tr><td style="padding:10px 14px;color:#888;">成交金額</td>
      <td style="padding:10px 14px;font-size:20px;font-weight:bold;color:#e63946;">
          NT${price_twd:,.0f}</td></tr>
  {case_row}
  <tr style="background:#f7f8fa;"><td style="padding:10px 14px;color:#888;">合計</td>
      <td style="padding:10px 14px;font-weight:bold;">NT${total:,.0f}</td></tr>
</table>
<p style="color:#555;">我們會協助確認出貨，如有問題請聯絡客服。</p>
"""
    return _send(to_email, f"{SITE_NAME} — 成交通知（買家）", _base_html("成交成功 🎉", body))


def send_trade_matched_seller(
    to_email: str,
    set_id: str,
    card_number: str,
    grade: int,
    price_twd: float,
    fee_twd: float,
    condition: Optional[str] = None,
    trade_id: Optional[int] = None,
) -> bool:
    grade_label = {10: "PSA 10", 9: "PSA 9", 0: "Raw 未鑑定"}.get(grade, str(grade))
    cond_map = {"mint": "全新未拆", "near_mint": "近全新", "excellent": "優良",
                "good": "良好", "poor": "普通"}
    cond_note = f"  品相：{cond_map.get(condition, condition)}" if condition else ""
    net = price_twd - fee_twd
    id_note = f"（成交 #{trade_id}）" if trade_id else ""

    body = f"""
<p style="color:#555;">您的卡片已成功成交 {id_note}，請盡快出貨給買家。</p>
<table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fff;
              border-radius:8px;overflow:hidden;">
  <tr><td style="padding:10px 14px;color:#888;width:40%;">系列 / 卡號</td>
      <td style="padding:10px 14px;font-weight:bold;">{set_id} #{card_number}</td></tr>
  <tr style="background:#f7f8fa;"><td style="padding:10px 14px;color:#888;">等級{cond_note}</td>
      <td style="padding:10px 14px;font-weight:bold;">{grade_label}</td></tr>
  <tr><td style="padding:10px 14px;color:#888;">成交價</td>
      <td style="padding:10px 14px;font-size:20px;font-weight:bold;color:#e63946;">
          NT${price_twd:,.0f}</td></tr>
  <tr style="background:#f7f8fa;"><td style="padding:10px 14px;color:#888;">平台服務費（5%）</td>
      <td style="padding:10px 14px;color:#e63946;">－NT${fee_twd:,.0f}</td></tr>
  <tr><td style="padding:10px 14px;color:#888;">您實收</td>
      <td style="padding:10px 14px;font-size:18px;font-weight:bold;color:#2a9d8f;">
          NT${net:,.0f}</td></tr>
</table>
<p style="color:#555;">請在 3 個工作天內出貨，逾時系統可能自動取消此筆交易。</p>
"""
    return _send(to_email, f"{SITE_NAME} — 成交通知（賣家）", _base_html("成交成功，請準備出貨 📦", body))


# ──────────────────────────────────────────────
#  5. 管理員新訂單通知
# ──────────────────────────────────────────────

def send_admin_new_order(
    set_id: str,
    card_number: str,
    grade: int,
    price_twd: float,
    buyer_id: int,
    seller_id: int,
    add_protective_case: bool = False,
    condition: Optional[str] = None,
    trade_id: Optional[int] = None,
) -> bool:
    grade_label = {10: "PSA 10", 9: "PSA 9", 0: "Raw 未鑑定"}.get(grade, str(grade))
    cond_map = {"mint": "全新未拆", "near_mint": "近全新", "excellent": "優良",
                "good": "良好", "poor": "普通"}
    cond_note = f"  品相：{cond_map.get(condition, condition)}" if condition else ""
    case_note = "✅ 已加購保護殼 (+NT$50)" if add_protective_case else "—"
    id_note = f"#{trade_id}" if trade_id else "—"

    body = f"""
<p style="color:#555;">有新訂單成交，請確認並協助後續作業。</p>
<table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fff;
              border-radius:8px;overflow:hidden;">
  <tr><td style="padding:10px 14px;color:#888;width:40%;">成交單號</td>
      <td style="padding:10px 14px;font-weight:bold;">{id_note}</td></tr>
  <tr style="background:#f7f8fa;"><td style="padding:10px 14px;color:#888;">系列 / 卡號</td>
      <td style="padding:10px 14px;font-weight:bold;">{set_id} #{card_number}</td></tr>
  <tr><td style="padding:10px 14px;color:#888;">等級{cond_note}</td>
      <td style="padding:10px 14px;font-weight:bold;">{grade_label}</td></tr>
  <tr style="background:#f7f8fa;"><td style="padding:10px 14px;color:#888;">成交價</td>
      <td style="padding:10px 14px;font-size:20px;font-weight:bold;color:#e63946;">
          NT${price_twd:,.0f}</td></tr>
  <tr><td style="padding:10px 14px;color:#888;">保護殼加購</td>
      <td style="padding:10px 14px;">{case_note}</td></tr>
  <tr style="background:#f7f8fa;"><td style="padding:10px 14px;color:#888;">買家 ID</td>
      <td style="padding:10px 14px;">{buyer_id}</td></tr>
  <tr><td style="padding:10px 14px;color:#888;">賣家 ID</td>
      <td style="padding:10px 14px;">{seller_id}</td></tr>
</table>
"""
    return _send(ADMIN_NOTIFY_EMAIL, f"[{SITE_NAME}] 新訂單成交 #{id_note}", _base_html("🛒 新訂單成交通知", body))
