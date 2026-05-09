"""OCR 驗證模組：匯入價格資料前比對卡圖防誤配

工作流：
  1. 給定一個 listing_url（含圖）+ 預期的 (set_id, card_number)
  2. 下載圖、OCR 底部
  3. 抽 (card_number, total, rarity, set_code)
  4. 跟 DB 中 card_list 該卡的 OCR 指紋比對
  5. 不一致 → reject 匯入（log 警告）

也可給 reference image URL 直接比對，不用 listing。

使用：
  from app.scraper.ocr_verify import verify_card_match
  ok, reason = verify_card_match(set_id, card_number, listing_image_url)
"""
from __future__ import annotations

import re
import sqlite3
import urllib.request
from io import BytesIO
from pathlib import Path
from threading import local
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent.parent / "cards.db"
TMP_DIR = Path(__file__).resolve().parent.parent.parent / "tmp_ocr_verify"
TMP_DIR.mkdir(exist_ok=True)

META_RE = re.compile(
    r'([A-Za-z][\w-]{1,5})?\s*(\d{1,4}[a-z]?)\s*/\s*(\d{1,4})\s*([A-Z]{1,5})?'
)
OCR_FIX_MAP = {
    'JMUR': 'MUR', 'IMUR': 'MUR', 'NUR': 'MUR',
    'IMA': 'MA', 'JMA': 'MA',
    'ISR': 'SR',
}

_tls = local()
def _get_ocr():
    if not hasattr(_tls, 'ocr'):
        from rapidocr_onnxruntime import RapidOCR
        _tls.ocr = RapidOCR()
    return _tls.ocr


def crop_bottom(img_path: Path, dest: Path, ratio: float = 0.20) -> bool:
    try:
        from PIL import Image
        img = Image.open(img_path)
        w, h = img.size
        cropped = img.crop((0, int(h * (1 - ratio)), w, h))
        if cropped.size[0] < 400:
            scale = 400 / cropped.size[0]
            cropped = cropped.resize((400, int(cropped.size[1] * scale)))
        cropped.save(dest)
        return True
    except Exception:
        return False


def parse_meta(ocr_results) -> dict:
    """從 OCR 結果抽 set_code / card_number / total / rarity"""
    out = {'set_code': None, 'card_number': None, 'total': None, 'rarity': None}
    if not ocr_results: return out
    for box, text, conf in ocr_results:
        if not isinstance(text, str): continue
        text_clean = text.strip().replace(' ', '')
        m = META_RE.search(text_clean)
        if m:
            sc, cn, tot, rar = m.groups()
            if sc and not out['set_code'] and not sc.isdigit() and len(sc) <= 5:
                out['set_code'] = sc
            if cn and not out['card_number']:
                out['card_number'] = cn.lstrip('0') or '0'
            if tot and not out['total']:
                out['total'] = tot.lstrip('0') or '0'
            if rar and not out['rarity']:
                out['rarity'] = OCR_FIX_MAP.get(rar.upper(), rar.upper())
    return out


def ocr_image(image_url: str) -> Optional[dict]:
    """下載 + OCR + 解析"""
    tmp = TMP_DIR / f"v_{abs(hash(image_url))}.png"
    cropped = TMP_DIR / f"v_{abs(hash(image_url))}_c.png"
    try:
        urllib.request.urlretrieve(image_url, tmp)
        if not crop_bottom(tmp, cropped): return None
        result, _ = _get_ocr()(str(cropped))
        return parse_meta(result)
    except Exception:
        return None
    finally:
        tmp.unlink(missing_ok=True)
        cropped.unlink(missing_ok=True)


def get_card_fingerprint(set_id: str, card_number: str) -> Optional[dict]:
    """從 DB 取得這張卡的 OCR 指紋（之前 backfill 抓到的）"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT card_number, rarity_ocr, set_code_ocr, ocr_full_text FROM card_list WHERE set_id=? AND card_number=?",
            (set_id, card_number),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def verify_card_match(set_id: str, card_number: str, listing_image_url: str) -> tuple[bool, str]:
    """匯入前驗證 listing 圖跟 DB 中我們的卡是否一致

    比對：
      - 卡號 (card_number) 必須相同
      - rarity 若兩邊都有則必須相同
      - set_code 若兩邊都有則必須相同

    回 (ok, reason)
    """
    db_meta = get_card_fingerprint(set_id, card_number)
    if not db_meta:
        return (True, "DB 沒指紋；passthrough")  # 還沒 OCR 過 → 不能驗 → 放行

    listing_meta = ocr_image(listing_image_url)
    if not listing_meta:
        return (True, "listing OCR 失敗；passthrough")

    # 1. card_number 必須一致
    db_cn = str(card_number).lstrip('0') or '0'
    listing_cn = listing_meta.get('card_number')
    if listing_cn and listing_cn != db_cn:
        return (False, f"卡號不符 DB={db_cn} listing={listing_cn}")

    # 2. rarity 比對（雙方都有才比）
    db_r = db_meta.get('rarity_ocr')
    listing_r = listing_meta.get('rarity')
    if db_r and listing_r and db_r != listing_r:
        return (False, f"rarity 不符 DB={db_r} listing={listing_r}")

    # 3. set_code 比對
    db_sc = db_meta.get('set_code_ocr')
    listing_sc = listing_meta.get('set_code')
    if db_sc and listing_sc and db_sc.lower() != listing_sc.lower():
        return (False, f"set_code 不符 DB={db_sc} listing={listing_sc}")

    return (True, "match")


if __name__ == "__main__":
    # smoke test：自己對自己應該 ok
    sets = [
        ('jp-MEGA-Dream-ex', '230'),  # MA
        ('jp-MEGA-Dream-ex', '250'),  # MUR
        ('jp-Inferno-X', '110'),
    ]
    conn = sqlite3.connect(str(DB_PATH))
    for set_id, cn in sets:
        url = conn.execute(
            "SELECT image_url FROM card_list WHERE set_id=? AND card_number=?",
            (set_id, cn),
        ).fetchone()
        if not url: continue
        ok, reason = verify_card_match(set_id, cn, url[0])
        print(f'{set_id} #{cn} self-match: {ok} ({reason})')
    conn.close()
