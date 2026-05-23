# JP 新卡盒自動補資料系統 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 寫一套可重用、可排程、可手動觸發的「補 JP 卡盒進 DB」系統、用第一個例子(M5「アビスアイ」深淵之眼擴充包 + M4 補漏卡)驗證。

**Architecture:** 三層解耦 — 偵測(SNKR 熱門爬完發現新 set_code)→ 執行(`app/scraper/jp_set_backfill.py` 主模組 + `jp_set_sources.py` 各來源爬蟲)→ 通知(`set_backfill_jobs` 表狀態 + admin endpoints + 前端角標)。

**Tech Stack:** Python httpx async / aiosqlite / FastAPI / 正則(術語:regex)解析 HTML。對應 spec:`docs/superpowers/specs/2026-05-24-jp-set-backfill-design.md`。

---

## 檔案結構規劃

### 新建檔案

| 路徑 | 角色 |
|---|---|
| `app/scraper/jp_set_backfill.py` | 主模組:三個 entry points(scrape_set / scrape_missing_cards / daily_backfill_loop)+ state machine 推進 + 常數 |
| `app/scraper/jp_set_sources.py` | 三個來源網站爬蟲(pokemon-card.com / artofpkm.com / 52poke wiki)、每個一個 class |
| `_test_jp_set_backfill_m5.py` | 臨時驗證腳本:dry-run M5 跟 commit-write M5 |
| `_test_jp_set_backfill_m4_extra.py` | 臨時驗證腳本:M4 補漏卡 |

### 修改檔案

| 路徑 | 改動 |
|---|---|
| `app/database.py` | 加 `set_backfill_jobs` 表 + idempotent ALTER |
| `app/main.py` | 加 3 個 admin endpoints、`_refresh_snkr_hot_items` 補偵測邏輯、lifespan 註冊排程 job、`/api/snkr/hot` 多回 `backfill_status` |
| `卡波/index.html` | `loadTrendingCarousel` 加「🕒 補資料中」角標邏輯 |

---

## Task 1:加 set_backfill_jobs 表

**Files:**
- Modify: `app/database.py`(找 `snkr_hot_items` 區段、加在後面)
- 同步:用 PowerShell 對既有 cards.db 跑 CREATE / ALTER

- [ ] **Step 1: 編輯 app/database.py 加 schema**

在 `snkr_hot_items` 後加:

```python
    # ========== set_backfill_jobs:補卡盒任務排隊表(2026-05-24 加)==========
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS set_backfill_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_code TEXT NOT NULL,
            source_hint TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            cards_scraped INTEGER DEFAULT 0,
            cards_translated INTEGER DEFAULT 0,
            error_msg TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sbj_status ON set_backfill_jobs(status, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sbj_set_code ON set_backfill_jobs(set_code)")
```

- [ ] **Step 2: 同步建表進既有 cards.db**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
c.execute('''CREATE TABLE IF NOT EXISTS set_backfill_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_code TEXT NOT NULL,
    source_hint TEXT,
    status TEXT NOT NULL DEFAULT \"pending\",
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    cards_scraped INTEGER DEFAULT 0,
    cards_translated INTEGER DEFAULT 0,
    error_msg TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
c.execute('CREATE INDEX IF NOT EXISTS idx_sbj_status ON set_backfill_jobs(status, created_at)')
c.execute('CREATE INDEX IF NOT EXISTS idx_sbj_set_code ON set_backfill_jobs(set_code)')
c.commit()
print(c.execute('SELECT sql FROM sqlite_master WHERE name=\"set_backfill_jobs\"').fetchone())
"
```

Expected: 印出 CREATE TABLE 完整 SQL、確認表建好

- [ ] **Step 3: Backup cards.db 後 commit**

```bash
cp cards.db cards.db.before-jp-set-backfill-20260524
git add app/database.py
git commit -m "db: 加 set_backfill_jobs 表追蹤補卡盒任務"
```

---

## Task 2:建 jp_set_backfill.py 骨架 + 常數

**Files:**
- Create: `app/scraper/jp_set_backfill.py`

- [ ] **Step 1: 寫主檔骨架**

```python
"""JP 新卡盒自動補資料模組(2026-05-24)。

入口:
  scrape_set(set_code)         - 完整補一個新卡盒進 DB
  scrape_missing_cards(set_code) - 補既有卡盒漏卡
  daily_backfill_loop()        - 排程入口、跑 pending queue

對應 spec:docs/superpowers/specs/2026-05-24-jp-set-backfill-design.md
"""
import asyncio
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiosqlite

DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "cards.db")

# ========== 設定值(調整時改這裡)==========
MAX_SETS_PER_DAY = 2          # 一天最多補幾個卡盒
SLEEP_BETWEEN_CARDS = 2.0     # 卡之間隔幾秒
MAX_RETRIES_PER_CARD = 3      # 每張卡最多重試幾次
RETRY_BACKOFF = [2, 10, 30]   # 重試間隔(秒)
MAX_CONSECUTIVE_FAILS = 5     # 連續失敗多少張就放棄整任務
RUNNING_STUCK_THRESHOLD_MIN = 30  # 「running 但 X 分鐘前」判定死掉
DAILY_RUN_HOUR = 3            # 排程觸發時間(凌晨 03:00)


# ========== 主入口(後續 Task 9-10-12 實作)==========

async def scrape_set(set_code: str, source_hint: str = "manual") -> dict:
    """完整補一個新卡盒進 DB。Task 9 實作。"""
    raise NotImplementedError("Task 9")


async def scrape_missing_cards(set_code: str, source_hint: str = "manual") -> dict:
    """補既有卡盒漏卡。Task 10 實作。"""
    raise NotImplementedError("Task 10")


async def daily_backfill_loop():
    """排程入口、跑 pending queue。Task 12 實作。"""
    raise NotImplementedError("Task 12")


# ========== 內部 helper(Task 3+ 實作)==========

async def allocate_new_pg(set_code: str) -> int:
    """分配新 pg。Task 3 實作。"""
    raise NotImplementedError("Task 3")


async def detect_dead_running_jobs():
    """撈 status='running' 但 started_at > 30 min 前的、判定死掉、改回 pending。"""
    cutoff = datetime.now() - timedelta(minutes=RUNNING_STUCK_THRESHOLD_MIN)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE set_backfill_jobs SET status='pending', started_at=NULL "
            "WHERE status='running' AND started_at < ?",
            (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
        )
        await db.commit()


async def enqueue_set(set_code: str, source_hint: str) -> Optional[int]:
    """加任務進排隊。已存在 pending/running 就跳過、回 None。"""
    async with aiosqlite.connect(DB_PATH) as db:
        existing = await (await db.execute(
            "SELECT id FROM set_backfill_jobs "
            "WHERE set_code = ? AND status IN ('pending', 'running') LIMIT 1",
            (set_code,),
        )).fetchone()
        if existing:
            return None
        cur = await db.execute(
            "INSERT INTO set_backfill_jobs (set_code, source_hint, status) "
            "VALUES (?, ?, 'pending')",
            (set_code, source_hint),
        )
        await db.commit()
        return cur.lastrowid
```

- [ ] **Step 2: 確認 import 正確、Python 能 load**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "from app.scraper import jp_set_backfill; print('OK', dir(jp_set_backfill))"
```

Expected: 印出 `OK` + 函式清單,沒有 ImportError

- [ ] **Step 3: 寫 _test_enqueue 驗證 enqueue_set 去重**

新建 `_test_jp_set_backfill_enqueue.py`:

```python
import asyncio
from app.scraper.jp_set_backfill import enqueue_set
import sqlite3

async def main():
    c = sqlite3.connect('cards.db')
    c.execute("DELETE FROM set_backfill_jobs WHERE set_code='TEST'")
    c.commit()
    c.close()

    id1 = await enqueue_set("TEST", "test")
    print(f"first INSERT: id={id1} (應該 > 0)")
    id2 = await enqueue_set("TEST", "test")
    print(f"second INSERT: id={id2} (應該 None,去重)")
    assert id1 is not None and id2 is None
    print("OK")

    c = sqlite3.connect('cards.db')
    c.execute("DELETE FROM set_backfill_jobs WHERE set_code='TEST'")
    c.commit()
    c.close()

asyncio.run(main())
```

跑:`PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _test_jp_set_backfill_enqueue.py`

Expected: 印 `first INSERT: id=N` + `second INSERT: id=None` + `OK`

- [ ] **Step 4: Commit**

```bash
git add app/scraper/jp_set_backfill.py
git commit -m "scraper: jp_set_backfill 模組骨架 + enqueue/detect_dead_running"
```

---

## Task 3:寫 allocate_new_pg

**Files:**
- Modify: `app/scraper/jp_set_backfill.py`(替換 Task 2 raise NotImplementedError 那段)

- [ ] **Step 1: 實作 allocate_new_pg**

```python
async def allocate_new_pg(set_code: str) -> int:
    """分配新 pg。

    普通擴充包(set_code 不以 -P 結尾):取 1-999 區段現有 max + 1
    Promo set(以 -P 結尾):取 9100+ 區段現有 max + 1(避開既有 9001-9099)
    """
    is_promo = set_code.endswith('-P') or set_code.endswith('-PROMO')
    async with aiosqlite.connect(DB_PATH) as db:
        if is_promo:
            row = await (await db.execute(
                "SELECT MAX(CAST(pg AS INTEGER)) FROM jp_card_list_set "
                "WHERE CAST(pg AS INTEGER) >= 9100"
            )).fetchone()
            current_max = row[0] if row[0] else 9099
            return max(9100, current_max + 1)
        else:
            row = await (await db.execute(
                "SELECT MAX(CAST(pg AS INTEGER)) FROM jp_card_list_set "
                "WHERE CAST(pg AS INTEGER) < 9000"
            )).fetchone()
            current_max = row[0] if row[0] else 0
            return current_max + 1
```

- [ ] **Step 2: 寫 _test_allocate_pg 驗證**

新建 `_test_allocate_pg.py`:

```python
import asyncio
from app.scraper.jp_set_backfill import allocate_new_pg

async def main():
    pg_normal = await allocate_new_pg("M5")
    pg_promo = await allocate_new_pg("SV-P")
    print(f"M5 (擴充包) → pg={pg_normal} (應該 954)")
    print(f"SV-P (promo) → pg={pg_promo} (應該 9100 或更高)")
    assert pg_normal == 954, f"預期 954 實際 {pg_normal}"
    assert pg_promo >= 9100, f"預期 ≥9100 實際 {pg_promo}"
    print("OK")

asyncio.run(main())
```

跑:`PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _test_allocate_pg.py`

Expected: 印 `M5 → pg=954` + `SV-P → pg=9100` + `OK`

- [ ] **Step 3: Commit**

```bash
git add app/scraper/jp_set_backfill.py
git commit -m "scraper: allocate_new_pg 分配新 pg(普通 / promo 分區段)"
```

---

## Task 4:寫 PokemonCardComSource — 搜尋頁

**Files:**
- Create: `app/scraper/jp_set_sources.py`

- [ ] **Step 1: 寫 source class 骨架 + 搜尋方法**

```python
"""JP 卡盒資料三個來源網站爬蟲。

來源:
  PokemonCardComSource - pokemon-card.com(主要資料、cardID / name_jp / rarity / image_id)
  ArtOfPkmSource       - artofpkm.com(HD 卡圖、image_url 升級)
  Bulbapedia52pokeSource - 52poke wiki(中譯)

設計:每個 source 一個 class、共用 retry 邏輯、httpx async。
"""
import re
import asyncio
from typing import Optional
from urllib.parse import quote, urljoin
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0"


class PokemonCardComSource:
    """pokemon-card.com 官方來源。"""

    BASE = "https://www.pokemon-card.com"

    async def search_set_by_jp_name(self, jp_name: str) -> Optional[dict]:
        """從 pokemon-card.com 搜尋頁找卡盒、回傳 set_url + logo_url + release_date。

        例:jp_name='アビスアイ' → 找到「拡張パック「アビスアイ」」對應卡盒頁。
        """
        url = f"{self.BASE}/card-search/index.php?keyword={quote(jp_name)}"
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            html = r.text

        # pokemon-card.com 搜尋結果用 <div class="ResultsList"> 包卡盒
        # 但通常一個卡盒就一個 link、grep 「products/2025/images/m5」之類的 image
        # 實際 selector 要看實際頁面確認、這裡用 placeholder pattern
        m = re.search(
            r'href="(/card-search/index\.php\?[^"]*expansionCodes?=([^&"]+)[^"]*)"',
            html,
        )
        if not m:
            return None
        return {
            "set_url": urljoin(self.BASE, m.group(1)),
            "expansion_code": m.group(2),
        }
```

- [ ] **Step 2: 手動跑 search 驗證對 M5 的搜尋**

新建 `_test_pokemon_card_search.py`:

```python
import asyncio
from app.scraper.jp_set_sources import PokemonCardComSource

async def main():
    src = PokemonCardComSource()
    # 試三個關鍵字(SNKR title 提到的 set 名)
    for q in ["アビスアイ", "ニンジャスピナー", "インフェルノX"]:
        result = await src.search_set_by_jp_name(q)
        print(f"{q!r:30s} → {result}")

asyncio.run(main())
```

跑:`PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _test_pokemon_card_search.py`

Expected: 至少印出 expansion_code(可能是 M5、M4、M2 之類)、若 selector 不對就要調 regex

**若 regex 沒命中**:看 `r.text` 實際 HTML 結構、用 regex / BeautifulSoup 重寫 search 邏輯。記得寫進注意事項給後續 task。

- [ ] **Step 3: Commit**

```bash
git add app/scraper/jp_set_sources.py
git commit -m "scraper: PokemonCardComSource 搜尋頁找卡盒"
```

---

## Task 5:寫 PokemonCardComSource — 卡片列表

**Files:**
- Modify: `app/scraper/jp_set_sources.py`(類別內加方法)

- [ ] **Step 1: 加 scrape_card_list 方法**

```python
    async def scrape_card_list(self, expansion_code: str) -> list[dict]:
        """從 pokemon-card.com 卡片列表頁抓所有卡的基本資料。

        URL pattern:/card-search/index.php?expansionCodes={code}
        回:[{cardID, name_jp, card_number, rarity, image_id, image_url}, ...]
        """
        url = f"{self.BASE}/card-search/index.php?expansionCodes={expansion_code}"
        cards = []
        page = 1
        while True:
            page_url = f"{url}&pageNo={page}"
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
                r = await client.get(page_url)
                if r.status_code != 200:
                    break
                html = r.text

            # 每張卡是 <li ...><a href="/card-search/details.php/card/{cardID}/..."><img src=".../{image_id}.jpg" alt="{name_jp}"></a></li>
            cards_on_page = re.findall(
                r'href="/card-search/details\.php/card/(\d+)/[^"]*"[^>]*>\s*'
                r'<img[^>]+src="[^"]*?/(\d+)\.jpg"[^>]+alt="([^"]+)"',
                html,
            )
            if not cards_on_page:
                break

            for cardID, image_id, name_jp in cards_on_page:
                cards.append({
                    "cardID": int(cardID),
                    "image_id": image_id,
                    "name_jp": name_jp,
                    "card_number": None,  # 詳情頁才有,Task 6 補
                    "rarity": None,
                    "image_url": f"{self.BASE}/assets/images/card_images/large/{image_id[:3]}/{image_id}.jpg",
                })

            page += 1
            await asyncio.sleep(2.0)
            if page > 20:  # 防止無限迴圈
                break

        return cards
```

- [ ] **Step 2: 跑 _test_card_list 驗證對 M2 的爬取**

新建 `_test_card_list.py`:

```python
import asyncio
from app.scraper.jp_set_sources import PokemonCardComSource

async def main():
    src = PokemonCardComSource()
    cards = await src.scrape_card_list("M2")  # 既有 set,應該爬到 116 卡
    print(f"M2 拿到 {len(cards)} 張卡(預期 ~116)")
    for c in cards[:3]:
        print(f"  cardID={c['cardID']} image_id={c['image_id']} name_jp={c['name_jp']}")
    assert len(cards) >= 80, f"M2 卡數太少 {len(cards)}"
    print("OK")

asyncio.run(main())
```

Expected: 印 `M2 拿到 116 張` + 前 3 張卡資料 + `OK`

- [ ] **Step 3: Commit**

```bash
git add app/scraper/jp_set_sources.py
git commit -m "scraper: PokemonCardComSource 卡片列表頁解析"
```

---

## Task 6:寫 PokemonCardComSource — 卡片詳情頁(card_number / rarity)

**Files:**
- Modify: `app/scraper/jp_set_sources.py`(類別內加方法、複用 jp_detail_crawl_v2 解析邏輯)

- [ ] **Step 1: 加 scrape_card_detail 方法**

```python
    async def scrape_card_detail(self, card_id: int) -> dict:
        """爬單張卡詳情頁、抓 card_number / rarity / hp / type / illustrator。

        URL pattern:/card-search/details.php/card/{card_id}/regu/all
        """
        url = f"{self.BASE}/card-search/details.php/card/{card_id}/regu/all"
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return {}
            html = r.text

        out = {}

        # card_number: 通常在 <span class="num">XXX/YYY</span>
        m = re.search(r'class="num"[^>]*>\s*(\d+)\s*/\s*(\d+)\s*</', html)
        if m:
            out["card_number"] = str(int(m.group(1)))

        # rarity: 從 .icon-rare-X class 或 .RaritySymbol 解析
        m = re.search(r'class="icon-rare-(\w+)"', html)
        if m:
            out["rarity"] = m.group(1).upper()  # 例 SAR / SR / UR
        else:
            # fallback: 從稀有度文字
            m = re.search(r'稀有度[^<]*<[^>]+>([^<]+)<', html)
            if m:
                out["rarity"] = m.group(1).strip()

        # hp(可選)
        m = re.search(r'class="hp-num"[^>]*>\s*(\d+)\s*</', html)
        if m:
            out["hp"] = int(m.group(1))

        return out

    async def scrape_card_with_detail(self, card_id: int) -> dict:
        """爬一張卡的完整資料(列表頁的 + 詳情頁的)。retry 邏輯。"""
        for attempt in range(3):
            try:
                detail = await self.scrape_card_detail(card_id)
                return detail
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep([2, 10, 30][attempt])
                else:
                    raise
        return {}
```

- [ ] **Step 2: 跑 _test_card_detail 驗證對 M2 #110 (MEGA リザードン X ex SAR)**

新建 `_test_card_detail.py`:

```python
import asyncio
from app.scraper.jp_set_sources import PokemonCardComSource

async def main():
    src = PokemonCardComSource()
    # M2 #110 是 メガリザードンXex SAR cardID 應該是 48450 左右
    # 先用 list 抓到 cardID 對應 card_number=110 那張
    cards = await src.scrape_card_list("M2")
    target = None
    for c in cards:
        d = await src.scrape_card_with_detail(c["cardID"])
        if d.get("card_number") == "110":
            target = {**c, **d}
            break
    print(f"target = {target}")
    assert target is not None
    assert target["card_number"] == "110"
    assert "SAR" in (target.get("rarity") or ""), f"rarity 應該 SAR 實際 {target.get('rarity')}"
    print("OK")

asyncio.run(main())
```

Expected: 印 target dict 含 card_number=110 + rarity 含 SAR + `OK`

- [ ] **Step 3: Commit**

```bash
git add app/scraper/jp_set_sources.py
git commit -m "scraper: PokemonCardComSource 卡片詳情頁 + retry"
```

---

## Task 7:寫 ArtOfPkmSource — HD 圖片 fallback

**Files:**
- Modify: `app/scraper/jp_set_sources.py`(加 class)

- [ ] **Step 1: 加 ArtOfPkmSource class**

```python
class ArtOfPkmSource:
    """artofpkm.com 來源:用來抓 HD 卡圖(若 pokemon-card.com 沒提供高解析)。"""

    BASE = "https://www.artofpkm.com"

    async def get_hd_image(self, jp_set_name: str, card_number: str) -> Optional[str]:
        """從 artofpkm.com 找對應 set 的對應 card_number HD 圖 URL。

        artofpkm slug 規則:jp_set_name → slugify(URL-safe)
        例:「拡張パック アビスアイ」→ slug 可能是 'jp-Abyss-Eye' 之類
        """
        # 第一步:從 artofpkm sets 頁找對應 set
        sets_url = f"{self.BASE}/cards"
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
            r = await client.get(sets_url)
            if r.status_code != 200:
                return None
            html = r.text

        # 找出包含 jp_set_name 部分字串的 link
        # 例:href="/cards/jp-Abyss-Eye" + alt="拡張パック「アビスアイ」"
        # 兩個 escape 處理:用 jp_set_name 內 keyword 找
        keyword = re.sub(r'[「」"\'』『]', '', jp_set_name).strip()
        if not keyword:
            return None

        # 抓所有 set link、找 alt / title 含 keyword 的
        for m in re.finditer(r'href="(/cards/[^"]+)"[^>]*>([^<]*)', html):
            set_link, link_text = m.group(1), m.group(2)
            if keyword[:4] in link_text:  # 簡易 partial match
                set_detail_url = urljoin(self.BASE, set_link)
                return await self._find_card_image(set_detail_url, card_number)
        return None

    async def _find_card_image(self, set_detail_url: str, card_number: str) -> Optional[str]:
        """在 set 詳情頁找對應 card_number 的卡圖。"""
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
            r = await client.get(set_detail_url)
            if r.status_code != 200:
                return None
            html = r.text

        # artofpkm 卡圖是 <img src="..." alt="#XXX ..."> 或類似
        for m in re.finditer(
            r'<img[^>]+src="([^"]+)"[^>]+alt="#?(\d+)[^"]*"',
            html,
        ):
            img_src, cn = m.group(1), m.group(2)
            if str(int(cn)) == card_number:
                return img_src if img_src.startswith("http") else urljoin(self.BASE, img_src)
        return None
```

**注意**:artofpkm 的實際 HTML 結構要靠 spike(實際開頁面看)、上面 regex 可能不對。Task 7 動工時先 WebFetch / 手動 curl 確認頁面結構,再調整 regex。

- [ ] **Step 2: 跑 _test_artofpkm 驗證**

新建 `_test_artofpkm.py`:

```python
import asyncio
from app.scraper.jp_set_sources import ArtOfPkmSource

async def main():
    src = ArtOfPkmSource()
    # 試 M2 #110(SAR Mega Charizard X ex)
    img = await src.get_hd_image("拡張パック「インフェルノX」", "110")
    print(f"M2 #110 HD 圖:{img}")
    # 預期:回一個 https://... .jpg URL 或 None(若 set 名對映不到)
    if img:
        print("OK")
    else:
        print("WARN: 沒找到 HD 圖、不算錯誤(會 fallback pokemon-card 原圖)")

asyncio.run(main())
```

Expected: 回 URL 或 None(both 都算過、artofpkm 不一定有所有 set)

- [ ] **Step 3: Commit**

```bash
git add app/scraper/jp_set_sources.py
git commit -m "scraper: ArtOfPkmSource HD 圖片 fallback"
```

---

## Task 8:寫 Bulbapedia52pokeSource — 中譯抓取

**Files:**
- Modify: `app/scraper/jp_set_sources.py`(加 class)

- [ ] **Step 1: 加 Bulbapedia52pokeSource class**

```python
class Bulbapedia52pokeSource:
    """52poke wiki 來源:抓卡盒對應的中譯。

    對 jp set 用兩種頁:
      - 繁中版:wiki/「XXX(繁體中文版特典卡 / 擴充包)」
      - 日文版:wiki/「XXX_(TCG)」
    """

    BASE = "https://wiki.52poke.com"

    async def fetch_translations(self, jp_set_name: str) -> dict[str, str]:
        """回傳 {jp_card_name: zh_translation} 字典。

        策略:
        1. 先試繁中版頁(編號跟 asia 中文官網對齊、不可直接套 jp_card_list)
        2. 再試日文版頁(編號跟 jp 官方對齊、但翻譯內容是中文)
        """
        translations = {}

        # 策略 1:繁中版頁
        zh_html = await self._fetch_wiki_page(f"{jp_set_name}(TCG)")
        if zh_html:
            translations.update(self._parse_translation_table(zh_html))

        # 策略 2:日文版頁(如果繁中沒找到、或補翻譯)
        if len(translations) < 30:
            jp_html = await self._fetch_wiki_page(f"{jp_set_name}_(TCG)")
            if jp_html:
                translations.update(self._parse_translation_table(jp_html))

        return translations

    async def _fetch_wiki_page(self, page_title: str) -> Optional[str]:
        """抓 wiki 頁面 HTML(WebFetch 對 wiki 403、改用 httpx 加 referer)。"""
        url = f"{self.BASE}/wiki/{quote(page_title)}"
        headers = {
            "User-Agent": UA,
            "Referer": "https://wiki.52poke.com/",
            "Accept-Language": "zh-TW,zh;q=0.9",
        }
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            return r.text

    def _parse_translation_table(self, html: str) -> dict[str, str]:
        """從 wiki 卡片表格抓 {日文 → 中文} 對應。"""
        result = {}
        # wiki 卡盒表格通常是:
        # <tr><td>#XX</td><td>{中文卡名}</td><td>{日文卡名}</td>...</tr>
        # 兩個 cell 順序可能反、要 detect 哪個是日文(含 hiragana/katakana)
        rows = re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', html)
        for row in rows:
            cells = re.findall(r'<t[hd][^>]*>([\s\S]*?)</t[hd]>', row)
            if len(cells) < 3:
                continue
            # 去 HTML tag
            cleans = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            jp_cell = None
            zh_cell = None
            for c in cleans:
                if re.search(r'[぀-ヿ]', c):  # 含 hiragana/katakana = 日文
                    jp_cell = c
                elif re.match(r'^[一-鿿\w\s·・]+$', c) and 2 <= len(c) <= 30:
                    if not zh_cell:
                        zh_cell = c
            if jp_cell and zh_cell:
                result[jp_cell] = zh_cell
        return result
```

- [ ] **Step 2: 跑 _test_52poke 驗證對 M2 的抓取**

新建 `_test_52poke.py`:

```python
import asyncio
from app.scraper.jp_set_sources import Bulbapedia52pokeSource

async def main():
    src = Bulbapedia52pokeSource()
    trans = await src.fetch_translations("インフェルノX")
    print(f"抓到 {len(trans)} 條翻譯")
    for jp, zh in list(trans.items())[:5]:
        print(f"  {jp} → {zh}")
    assert len(trans) >= 5, "M2 應該至少有 5 條翻譯"
    print("OK")

asyncio.run(main())
```

Expected: 印 N 條翻譯 + 前 5 條 + `OK`(若 0 條表示 wiki 頁不存在或 selector 錯)

- [ ] **Step 3: Commit**

```bash
git add app/scraper/jp_set_sources.py
git commit -m "scraper: Bulbapedia52pokeSource 抓 wiki 中譯"
```

---

## Task 9:寫 scrape_set 主流程(整合 + retry + state machine)

**Files:**
- Modify: `app/scraper/jp_set_backfill.py`

- [ ] **Step 1: 實作 scrape_set**

```python
async def scrape_set(set_code: str, source_hint: str = "manual",
                     dry_run: bool = False) -> dict:
    """完整補一個新卡盒進 DB。

    Args:
        set_code: 例 'M5'
        source_hint: 'manual' / 'snkr_hot_detect' / 'm4_漏卡補抓'
        dry_run: True 不寫 DB、只列要做什麼

    Returns:
        {job_id, status, cards_scraped, cards_translated, error_msg}
    """
    from app.scraper.jp_set_sources import (
        PokemonCardComSource, ArtOfPkmSource, Bulbapedia52pokeSource
    )

    # ----- 1. 排隊 / 取 job -----
    job_id = await enqueue_set(set_code, source_hint)
    if job_id is None:
        # 已有 pending / running 任務、直接 retry 那筆
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute(
                "SELECT id FROM set_backfill_jobs "
                "WHERE set_code = ? AND status IN ('pending', 'running') LIMIT 1",
                (set_code,),
            )).fetchone()
            job_id = row[0] if row else None
        if not job_id:
            return {"status": "error", "error_msg": "無法建立 / 找到 job"}

    # ----- 2. 標記 running -----
    if not dry_run:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE set_backfill_jobs SET status='running', "
                "started_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,),
            )
            await db.commit()

    # ----- 3. 從 SNKR title 推 jp_set_name(暫用 set_code 當 keyword,Step 後續可改進)-----
    pcc = PokemonCardComSource()
    artofpkm = ArtOfPkmSource()
    wiki = Bulbapedia52pokeSource()

    # 試從 SNKR 既有 mapping / 既有 jp_card_list 拿 jp_set_name
    jp_set_name = await _guess_jp_set_name(set_code)
    if not jp_set_name:
        await _mark_failed(job_id, f"無法推測 jp_set_name for set_code={set_code}", dry_run)
        return {"status": "failed", "job_id": job_id,
                "error_msg": f"無法推測 jp_set_name for {set_code}"}

    # ----- 4. 搜尋 + 拿卡片列表 -----
    try:
        search_result = await pcc.search_set_by_jp_name(jp_set_name)
        if not search_result:
            await _mark_failed(job_id, f"pokemon-card.com 找不到 {jp_set_name}", dry_run)
            return {"status": "failed", "job_id": job_id,
                    "error_msg": f"來源網站找不到 {jp_set_name}"}

        cards = await pcc.scrape_card_list(search_result["expansion_code"])
    except Exception as e:
        await _mark_failed(job_id, f"搜尋 / 列表錯誤:{e}", dry_run)
        return {"status": "failed", "job_id": job_id, "error_msg": str(e)}

    if len(cards) == 0:
        await _mark_failed(job_id, "卡片列表回空", dry_run)
        return {"status": "failed", "job_id": job_id, "error_msg": "卡片列表回空"}

    # ----- 5. 分配 pg + 寫卡盒主表 -----
    new_pg = await allocate_new_pg(set_code)
    print(f"[backfill] 分配新 pg={new_pg} for set_code={set_code}")

    if not dry_run:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO jp_card_list_set (pg, name_jp, hit_cnt) "
                "VALUES (?, ?, ?)",
                (str(new_pg), jp_set_name, 0),
            )
            await db.commit()

    # ----- 6. 逐張卡抓詳情 + 寫進 jp_card_list -----
    cards_scraped = 0
    cards_translated = 0
    consecutive_fails = 0
    wiki_translations = await wiki.fetch_translations(jp_set_name)
    print(f"[backfill] wiki 抓到 {len(wiki_translations)} 條翻譯")

    for card in cards:
        try:
            for attempt in range(MAX_RETRIES_PER_CARD):
                try:
                    detail = await pcc.scrape_card_with_detail(card["cardID"])
                    card.update(detail)
                    break
                except Exception as e:
                    if attempt < MAX_RETRIES_PER_CARD - 1:
                        await asyncio.sleep(RETRY_BACKOFF[attempt])
                    else:
                        raise

            if not card.get("card_number"):
                consecutive_fails += 1
                if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                    await _mark_failed(job_id, f"連續 {MAX_CONSECUTIVE_FAILS} 張失敗", dry_run)
                    return {"status": "failed", "job_id": job_id,
                            "error_msg": f"連續 {MAX_CONSECUTIVE_FAILS} 張失敗"}
                continue

            consecutive_fails = 0

            # 抓 HD 圖(可選)
            hd_img = await artofpkm.get_hd_image(jp_set_name, card["card_number"])
            if hd_img:
                card["image_url"] = hd_img

            # 翻譯
            name_zh = wiki_translations.get(card["name_jp"])

            # 寫進 jp_card_list
            if not dry_run:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT OR IGNORE INTO jp_card_list "
                        "(cardID, pg, set_code, card_number, name_jp, rarity, "
                        " image_id, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (card["cardID"], str(new_pg), set_code, card["card_number"],
                         card["name_jp"], card.get("rarity"), card.get("image_id"),
                         "pokemon-card.com"),
                    )
                    await db.execute(
                        "UPDATE set_backfill_jobs SET cards_scraped=? "
                        "WHERE id=?",
                        (cards_scraped + 1, job_id),
                    )
                    await db.commit()

            cards_scraped += 1
            if name_zh:
                cards_translated += 1

            print(f"  [{cards_scraped}/{len(cards)}] #{card['card_number']} {card['name_jp']}"
                  + (f" → {name_zh}" if name_zh else ""))

            await asyncio.sleep(SLEEP_BETWEEN_CARDS)

        except Exception as e:
            print(f"  [skip] cardID={card['cardID']} err:{e}")
            consecutive_fails += 1
            if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                await _mark_failed(job_id, f"連續 {MAX_CONSECUTIVE_FAILS} 張失敗:{e}", dry_run)
                return {"status": "failed", "job_id": job_id,
                        "error_msg": f"連續失敗"}

    # ----- 7. 完成、UPDATE hit_cnt + 標 done -----
    if not dry_run:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE jp_card_list_set SET hit_cnt=? WHERE pg=?",
                (cards_scraped, str(new_pg)),
            )
            await db.execute(
                "UPDATE set_backfill_jobs SET status='done', "
                "finished_at=CURRENT_TIMESTAMP, cards_scraped=?, cards_translated=? "
                "WHERE id=?",
                (cards_scraped, cards_translated, job_id),
            )
            await db.commit()

    return {
        "status": "done",
        "job_id": job_id,
        "pg": new_pg,
        "cards_scraped": cards_scraped,
        "cards_translated": cards_translated,
    }


async def _guess_jp_set_name(set_code: str) -> Optional[str]:
    """從既有 SNKR title 推測 jp_set_name(用 snkr_hot_items 表)。"""
    async with aiosqlite.connect(DB_PATH) as db:
        # 例:title='メガダークライex SAR [M5 114/081](拡張パック「アビスアイ」)' → 抓「アビスアイ」
        row = await (await db.execute(
            "SELECT title FROM snkr_hot_items "
            "WHERE title LIKE ? COLLATE NOCASE "
            "AND title LIKE '%「%」%' ORDER BY fetched_at DESC LIMIT 1",
            (f"%[{set_code} %",),
        )).fetchone()
        if not row:
            return None
        m = re.search(r'「([^」]+)」', row[0])
        return m.group(1) if m else None


async def _mark_failed(job_id: int, error_msg: str, dry_run: bool):
    if dry_run:
        print(f"[dry-run] would mark job {job_id} failed: {error_msg}")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE set_backfill_jobs SET status='failed', "
            "finished_at=CURRENT_TIMESTAMP, error_msg=? WHERE id=?",
            (error_msg, job_id),
        )
        await db.commit()
```

- [ ] **Step 2: 跑 _test_jp_set_backfill_m5.py dry-run**

新建檔案內容:

```python
"""Dry-run M5 補卡盒、不寫 DB、只看流程能跑通"""
import asyncio
from app.scraper.jp_set_backfill import scrape_set

async def main():
    print("=== Dry-run M5 アビスアイ ===")
    result = await scrape_set("M5", "manual_dry_run", dry_run=True)
    print(f"result = {result}")
    assert result["status"] in ["done", "failed"], f"unexpected status {result['status']}"
    print("OK")

asyncio.run(main())
```

跑:`PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _test_jp_set_backfill_m5.py`

Expected: 流程跑通(印 80+ 卡 + 進度條)、最後 `OK`。若 failed 看 error_msg 排查。

- [ ] **Step 3: Commit**

```bash
git add app/scraper/jp_set_backfill.py
git commit -m "scraper: scrape_set 完整主流程(retry / state machine / 翻譯整合)"
```

---

## Task 10:寫 scrape_missing_cards(M4 補漏卡)

**Files:**
- Modify: `app/scraper/jp_set_backfill.py`

- [ ] **Step 1: 實作 scrape_missing_cards**

```python
async def scrape_missing_cards(set_code: str, source_hint: str = "manual") -> dict:
    """補既有卡盒漏卡。

    跟 scrape_set 差別:
      - 不分配新 pg、用既有的
      - INSERT OR IGNORE on (set_id, card_number)、原有跳過
      - 適用情境:M4 #84-114 SAR/UR/MUR 變體超出原 max card_number
    """
    from app.scraper.jp_set_sources import PokemonCardComSource

    # 找既有 pg
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT pg FROM jp_card_list WHERE set_code=? COLLATE NOCASE LIMIT 1",
            (set_code,),
        )).fetchone()
        if not row:
            return {"status": "error", "error_msg": f"既有 jp_card_list 沒 {set_code}"}
        existing_pg = row[0]

    job_id = await enqueue_set(f"{set_code}-extra", source_hint)
    if job_id is None:
        return {"status": "skip", "reason": "已在排隊"}

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE set_backfill_jobs SET status='running', started_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,),
        )
        await db.commit()

    # 抓全 set 卡片(列表頁拿到所有 cardID)
    pcc = PokemonCardComSource()
    jp_set_name = await _guess_jp_set_name(set_code)
    if not jp_set_name:
        await _mark_failed(job_id, f"無法推測 jp_set_name for {set_code}", False)
        return {"status": "failed", "error_msg": "無 jp_set_name"}

    search_result = await pcc.search_set_by_jp_name(jp_set_name)
    if not search_result:
        await _mark_failed(job_id, f"pokemon-card.com 找不到 {jp_set_name}", False)
        return {"status": "failed", "error_msg": "搜尋無結果"}

    cards = await pcc.scrape_card_list(search_result["expansion_code"])

    # 找出哪些是「新」的(既有 pg + 新 card_number)
    async with aiosqlite.connect(DB_PATH) as db:
        existing_rows = await (await db.execute(
            "SELECT card_number FROM jp_card_list WHERE pg=?",
            (str(existing_pg),),
        )).fetchall()
    existing_cns = set(r[0] for r in existing_rows)

    new_count = 0
    for card in cards:
        try:
            detail = await pcc.scrape_card_with_detail(card["cardID"])
            card.update(detail)
            cn = card.get("card_number")
            if not cn or cn in existing_cns:
                continue
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO jp_card_list "
                    "(cardID, pg, set_code, card_number, name_jp, rarity, image_id, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (card["cardID"], str(existing_pg), set_code, cn,
                     card["name_jp"], card.get("rarity"), card.get("image_id"),
                     "pokemon-card.com"),
                )
                await db.commit()
            new_count += 1
            print(f"  new #{cn} {card['name_jp']}")
            await asyncio.sleep(SLEEP_BETWEEN_CARDS)
        except Exception as e:
            print(f"  skip cardID={card['cardID']}: {e}")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE set_backfill_jobs SET status='done', "
            "finished_at=CURRENT_TIMESTAMP, cards_scraped=? WHERE id=?",
            (new_count, job_id),
        )
        await db.commit()

    return {"status": "done", "job_id": job_id, "new_cards": new_count, "pg": existing_pg}
```

- [ ] **Step 2: 跑 _test_jp_set_backfill_m4_extra.py**

新建檔案:

```python
import asyncio
from app.scraper.jp_set_backfill import scrape_missing_cards

async def main():
    print("=== M4 補漏卡 ===")
    result = await scrape_missing_cards("M4", "test_m4_extra")
    print(f"result = {result}")
    assert result["status"] == "done"
    print(f"新增 {result.get('new_cards', 0)} 張")
    print("OK")

asyncio.run(main())
```

Expected: 新增 30-31 張 (M4 #84-114 SAR/UR/MUR 變體) + `OK`

- [ ] **Step 3: Commit**

```bash
git add app/scraper/jp_set_backfill.py
git commit -m "scraper: scrape_missing_cards 補既有 set 漏卡"
```

---

## Task 11:接 SNKR 熱門爬蟲偵測新 set_code

**Files:**
- Modify: `app/main.py`(在 `_refresh_snkr_hot_items` 結尾加偵測邏輯)

- [ ] **Step 1: 改 _refresh_snkr_hot_items**

找到 `async def _refresh_snkr_hot_items() -> dict:` 函式、在最後 commit 完之後、加偵測:

```python
    # ----- 偵測新 set_code、自動排隊補卡盒(2026-05-24 加)-----
    try:
        from app.scraper.jp_set_backfill import enqueue_set
        async with aiosqlite.connect(DB_PATH) as db:
            # 撈本批次的所有 set_code(從 title 解析)
            rows = await (await db.execute(
                "SELECT DISTINCT title FROM snkr_hot_items WHERE batch_id=?",
                (batch_id,),
            )).fetchall()

        seen_codes = set()
        for r in rows:
            m = re.search(r"\[\s*([\w-]+)\s+\d+", r[0])
            if m:
                seen_codes.add(m.group(1))

        # 查每個 set_code 是否在 jp_card_list
        new_codes = []
        async with aiosqlite.connect(DB_PATH) as db:
            for code in seen_codes:
                exists = await (await db.execute(
                    "SELECT 1 FROM jp_card_list WHERE set_code = ? COLLATE NOCASE LIMIT 1",
                    (code,),
                )).fetchone()
                if not exists:
                    new_codes.append(code)

        # 排隊
        for code in new_codes:
            job_id = await enqueue_set(code, "snkr_hot_detect")
            if job_id:
                print(f"[snkr_hot] 偵測新 set_code={code}、加進排隊 job_id={job_id}")

    except Exception as e:
        print(f"[snkr_hot] 偵測新 set 失敗(不影響主流程):{e}")
```

- [ ] **Step 2: 重啟 API、手動觸發 refresh、看 log 是否報「偵測新 set」**

```powershell
$pidLine = netstat -ano | Select-String ":8000.*LISTENING" | Select-Object -First 1
if ($pidLine) { $tpid = ($pidLine -split '\s+')[-1]; Stop-Process -Id $tpid -Force }
Start-Sleep -Seconds 2
Start-Process -FilePath "./Python/bin/python.exe" -ArgumentList "run_api.py" -RedirectStandardOutput "_run_api.log" -NoNewWindow
Start-Sleep -Seconds 6
curl.exe -s -X POST http://localhost:8000/api/admin/snkr-hot/refresh
Start-Sleep -Seconds 5
Get-Content _run_api.log -Tail 20
```

Expected: log 含 `[snkr_hot] 偵測新 set_code=M5、加進排隊 job_id=N`

- [ ] **Step 3: 確認 DB 內有 M5 pending**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
c.row_factory = sqlite3.Row
for r in c.execute('SELECT * FROM set_backfill_jobs WHERE set_code=\"M5\"'):
    print(dict(r))
"
```

Expected: 有一筆 `set_code='M5', status='pending', source_hint='snkr_hot_detect'`

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "main: SNKR 熱門爬完偵測新 set_code、自動排隊補卡盒"
```

---

## Task 12:寫 daily_backfill_loop 排程

**Files:**
- Modify: `app/scraper/jp_set_backfill.py`

- [ ] **Step 1: 實作 daily_backfill_loop**

```python
async def daily_backfill_loop():
    """排程入口、每天清晨 03:00 跑一次。

    流程:
      1. 把 stuck 的 running job 改回 pending
      2. 取 status='pending' 最舊的 MAX_SETS_PER_DAY 個
      3. 對每個跑 scrape_set 或 scrape_missing_cards
      4. 跑完睡到明天同一時間
    """
    while True:
        try:
            await detect_dead_running_jobs()

            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                rows = await (await db.execute(
                    "SELECT id, set_code, source_hint FROM set_backfill_jobs "
                    "WHERE status='pending' ORDER BY created_at ASC LIMIT ?",
                    (MAX_SETS_PER_DAY,),
                )).fetchall()

            print(f"[backfill_loop] {datetime.now()} 取 {len(rows)} 個 pending job")
            for r in rows:
                set_code = r["set_code"]
                source_hint = r["source_hint"] or "scheduled"
                print(f"[backfill_loop] 開跑 {set_code} (source_hint={source_hint})")
                try:
                    if set_code.endswith("-extra"):
                        result = await scrape_missing_cards(
                            set_code[:-6], source_hint
                        )
                    else:
                        result = await scrape_set(set_code, source_hint)
                    print(f"[backfill_loop] {set_code} done: {result}")
                except Exception as e:
                    print(f"[backfill_loop] {set_code} 整任務失敗:{e}")

        except Exception as e:
            print(f"[backfill_loop] loop 錯誤(不退出):{e}")

        # 睡到明天 DAILY_RUN_HOUR
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(
            hour=DAILY_RUN_HOUR, minute=0, second=0, microsecond=0
        )
        sleep_seconds = (tomorrow - now).total_seconds()
        print(f"[backfill_loop] 睡到 {tomorrow}({sleep_seconds:.0f} 秒)")
        await asyncio.sleep(sleep_seconds)
```

- [ ] **Step 2: 修改 app/main.py lifespan 加註冊**

找到 `job_ctrl.register("ebay-revalidator", ...)` 那段、後面加:

```python
    from app.scraper.jp_set_backfill import daily_backfill_loop as _daily_set_backfill
    set_backfill_job = job_ctrl.register(
        "set-backfill-daily", "JP 卡盒每日自動補(03:00)", factory=_daily_set_backfill
    )
```

如果 `disable_jobs=False` 才 start(跟 ebay_job 一樣):

```python
    if disable_jobs:
        # ...既有
    else:
        ebay_job.start()
        sync_job.start()
        hot_job.start()
        set_backfill_job.start()  # 加這行
```

- [ ] **Step 3: 重啟 API、確認 job 已 register**

```powershell
$pidLine = netstat -ano | Select-String ":8000.*LISTENING" | Select-Object -First 1
if ($pidLine) { $tpid = ($pidLine -split '\s+')[-1]; Stop-Process -Id $tpid -Force }
Start-Sleep -Seconds 2
Start-Process -FilePath "./Python/bin/python.exe" -ArgumentList "run_api.py" -RedirectStandardOutput "_run_api.log" -NoNewWindow
Start-Sleep -Seconds 6
curl.exe -s http://localhost:8000/api/admin/jobs
```

Expected: 回傳 JSON 含 `set-backfill-daily` job(status: stopped、因 HTA 模式 disable_jobs=1)

- [ ] **Step 4: Commit**

```bash
git add app/scraper/jp_set_backfill.py app/main.py
git commit -m "scraper: daily_backfill_loop + lifespan 註冊 set-backfill-daily job"
```

---

## Task 13:加 GET /api/admin/set-backfill/status

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: 加 endpoint**

在 `/api/admin/snkr-hot/refresh` 附近加:

```python
@app.get("/api/admin/set-backfill/status")
async def get_set_backfill_status():
    """看 set backfill 任務的 queue / running / recent_done / recent_failed。"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        queue = await (await db.execute(
            "SELECT id, set_code, source_hint, status, created_at "
            "FROM set_backfill_jobs WHERE status='pending' "
            "ORDER BY created_at ASC LIMIT 20"
        )).fetchall()
        running = await (await db.execute(
            "SELECT id, set_code, status, started_at, cards_scraped, cards_translated, "
            "       CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER) AS elapsed_seconds "
            "FROM set_backfill_jobs WHERE status='running' ORDER BY started_at DESC"
        )).fetchall()
        recent_done = await (await db.execute(
            "SELECT id, set_code, status, cards_scraped, cards_translated, "
            "       finished_at, "
            "       CAST((julianday(finished_at) - julianday(started_at)) * 86400 AS INTEGER) AS elapsed_seconds "
            "FROM set_backfill_jobs WHERE status='done' "
            "ORDER BY finished_at DESC LIMIT 10"
        )).fetchall()
        recent_failed = await (await db.execute(
            "SELECT id, set_code, status, error_msg, finished_at "
            "FROM set_backfill_jobs WHERE status='failed' "
            "ORDER BY finished_at DESC LIMIT 10"
        )).fetchall()
    return {
        "queue": [dict(r) for r in queue],
        "running": [dict(r) for r in running],
        "recent_done": [dict(r) for r in recent_done],
        "recent_failed": [dict(r) for r in recent_failed],
    }
```

- [ ] **Step 2: 重啟 + 驗證**

```powershell
$pidLine = netstat -ano | Select-String ":8000.*LISTENING" | Select-Object -First 1
if ($pidLine) { $tpid = ($pidLine -split '\s+')[-1]; Stop-Process -Id $tpid -Force }
Start-Sleep -Seconds 2
Start-Process -FilePath "./Python/bin/python.exe" -ArgumentList "run_api.py" -RedirectStandardOutput "_run_api.log" -NoNewWindow
Start-Sleep -Seconds 6
curl.exe -s http://localhost:8000/api/admin/set-backfill/status
```

Expected: 回 JSON 含 4 個 list、應該至少 queue 有 M5(若 Task 11 已跑過)

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "api: GET /api/admin/set-backfill/status"
```

---

## Task 14:加 POST /api/admin/set-backfill/{set_code}

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: 加 endpoint**

```python
@app.post("/api/admin/set-backfill/{set_code}")
async def post_set_backfill_enqueue(set_code: str):
    """手動加 set 進排隊。已存在 pending/running 不重複加。

    回傳 job_id(若新增) / null(若已存在)。
    """
    from app.scraper.jp_set_backfill import enqueue_set
    job_id = await enqueue_set(set_code, "manual_admin")
    return {"set_code": set_code, "job_id": job_id,
            "msg": "enqueued" if job_id else "already pending/running"}
```

- [ ] **Step 2: 驗證**

```powershell
curl.exe -s -X POST http://localhost:8000/api/admin/set-backfill/M5
```

Expected: 回 `{"set_code":"M5","job_id":null,"msg":"already pending/running"}`(已加過、不重複)
試新 set:

```powershell
curl.exe -s -X POST http://localhost:8000/api/admin/set-backfill/TESTX
```

Expected: 回 `{"set_code":"TESTX","job_id":N,"msg":"enqueued"}`

Cleanup:
```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "import sqlite3; c=sqlite3.connect('cards.db'); c.execute(\"DELETE FROM set_backfill_jobs WHERE set_code='TESTX'\"); c.commit()"
```

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "api: POST /api/admin/set-backfill/{set_code} 手動觸發"
```

---

## Task 15:加 POST /api/admin/set-backfill/{id}/retry

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: 加 endpoint**

```python
@app.post("/api/admin/set-backfill/{job_id}/retry")
async def post_set_backfill_retry(job_id: int):
    """失敗任務 retry:status 改回 pending、清 error_msg。"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT set_code, status FROM set_backfill_jobs WHERE id=?",
            (job_id,),
        )).fetchone()
        if not row:
            return {"status": "error", "msg": "job_id not found"}
        if row[1] not in ("failed", "done"):
            return {"status": "error", "msg": f"job is {row[1]}, can't retry"}
        await db.execute(
            "UPDATE set_backfill_jobs SET status='pending', "
            "started_at=NULL, finished_at=NULL, error_msg=NULL "
            "WHERE id=?",
            (job_id,),
        )
        await db.commit()
    return {"status": "ok", "job_id": job_id, "set_code": row[0],
            "msg": "reset to pending"}
```

- [ ] **Step 2: 驗證**

```powershell
# 假設 job_id=2 是個 failed 任務
curl.exe -s -X POST http://localhost:8000/api/admin/set-backfill/2/retry
```

Expected: `{"status":"ok","job_id":2,"set_code":"...","msg":"reset to pending"}`

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "api: POST /api/admin/set-backfill/{id}/retry"
```

---

## Task 16:改 /api/snkr/hot 多回 backfill_status

**Files:**
- Modify: `app/main.py`(`get_snkr_hot` 函式)

- [ ] **Step 1: 改 SQL 加 LEFT JOIN**

找到 `get_snkr_hot` 內取 rows 的 SQL、改成:

```python
        rows = await (await db.execute(
            """SELECT s.rank, s.apparel_id, s.title, s.price_jpy, s.image_url,
                      s.is_box, s.set_id, s.card_number, s.fetched_at,
                      -- 從 title 解析 set_code、查 set_backfill_jobs 最新狀態
                      (SELECT status FROM set_backfill_jobs
                        WHERE set_code = (
                          SELECT REPLACE(
                            SUBSTR(s.title,
                                   INSTR(s.title, '[') + 1,
                                   INSTR(s.title, ']') - INSTR(s.title, '[') - 1),
                            ' ' || SUBSTR(s.title,
                                          INSTR(s.title, ' ', INSTR(s.title, '[') + 1) + 1,
                                          INSTR(s.title, ']') - INSTR(s.title, ' ', INSTR(s.title, '[') + 1) - 1),
                            ''
                          )
                        )
                        ORDER BY created_at DESC LIMIT 1
                      ) AS backfill_status
               FROM snkr_hot_items s WHERE s.batch_id=? ORDER BY s.rank ASC LIMIT ?""",
            (latest_batch["batch_id"], limit),
        )).fetchall()
```

**注意**:上面 SQL 內嵌 set_code 解析複雜。**改用 Python 後處理較乾淨**:

```python
        rows = await (await db.execute(
            """SELECT rank, apparel_id, title, price_jpy, image_url, is_box,
                      set_id, card_number, fetched_at
               FROM snkr_hot_items WHERE batch_id=? ORDER BY rank ASC LIMIT ?""",
            (latest_batch["batch_id"], limit),
        )).fetchall()

        # 從每個 title 解析 set_code、批次查 backfill_status
        items = [dict(r) for r in rows]
        set_codes = set()
        for it in items:
            m = re.search(r"\[\s*([\w-]+)\s+\d+", it["title"])
            if m:
                set_codes.add(m.group(1))

        backfill_map = {}
        if set_codes:
            placeholders = ",".join("?" * len(set_codes))
            bf_rows = await (await db.execute(
                f"SELECT set_code, status FROM set_backfill_jobs "
                f"WHERE set_code IN ({placeholders}) AND status IN ('pending', 'running') "
                f"ORDER BY created_at DESC",
                list(set_codes),
            )).fetchall()
            for sc, st in bf_rows:
                if sc not in backfill_map:
                    backfill_map[sc] = st  # 取第一個(最新的、因 ORDER BY DESC)

        for it in items:
            m = re.search(r"\[\s*([\w-]+)\s+\d+", it["title"])
            it["backfill_status"] = backfill_map.get(m.group(1)) if m else None
```

然後最後 return 把 `[dict(r) for r in rows]` 換成 `items`:

```python
    return {
        "items": items,
        "fetched_at": latest_batch["fetched_at"],
        "source": "snkr_hottest",
        "disclaimer": "資料整理自 SNKR 公開 API",
    }
```

- [ ] **Step 2: 重啟 + curl 驗證**

```powershell
$pidLine = netstat -ano | Select-String ":8000.*LISTENING" | Select-Object -First 1
if ($pidLine) { $tpid = ($pidLine -split '\s+')[-1]; Stop-Process -Id $tpid -Force }
Start-Sleep -Seconds 2
Start-Process -FilePath "./Python/bin/python.exe" -ArgumentList "run_api.py" -RedirectStandardOutput "_run_api.log" -NoNewWindow
Start-Sleep -Seconds 6
curl.exe -s "http://localhost:8000/api/snkr/hot?limit=10"
```

Expected: items 每筆多一個 `backfill_status` 欄位、M5 的卡應該是 `"backfill_status": "pending"`(或 running、若 Task 9 跑過)

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "api: /api/snkr/hot 多回 backfill_status 欄位"
```

---

## Task 17:改前端 loadTrendingCarousel 加角標邏輯

**Files:**
- Modify: `卡波/index.html`

- [ ] **Step 1: 改 onclick + 角標邏輯**

找到 `loadTrendingCarousel` 內 `box.innerHTML = items.map((c) => {` 區段、把 `extLabel` 邏輯升級成三種狀態:

```javascript
      // 有 set_id + card_number → 跳本站詳情、否則跳 SNKR
      const hasDetail = c.set_id && c.card_number;
      const clickAction = hasDetail
        ? `location.hash = '#/detail?set=${escHtml(c.set_id)}&card=${escHtml(c.card_number)}'`
        : `window.open('${apparelUrl}','_blank')`;

      // 三種角標狀態
      let extLabel = '';
      if (hasDetail) {
        // 有對映 = 不顯示角標
      } else if (c.backfill_status === 'pending' || c.backfill_status === 'running') {
        // 排隊 / 跑中 = 灰色「補資料中」
        extLabel = `<div style="position:absolute;bottom:6px;right:6px;z-index:2;background:rgba(80,80,80,0.85);color:#fff;font-size:9px;font-weight:700;padding:2px 5px;border-radius:3px">🕒 補資料中</div>`;
      } else {
        // 沒對映也沒在排隊 = 「↗ SNKR」
        extLabel = `<div style="position:absolute;bottom:6px;right:6px;z-index:2;background:rgba(0,0,0,0.55);color:#fff;font-size:9px;font-weight:700;padding:2px 5px;border-radius:3px">↗ SNKR</div>`;
      }
```

(保留既有 rankBadge / boxBadge / img / 卡名 / 價格的 HTML 段、只動 `extLabel` 部分)

- [ ] **Step 2: Reload 瀏覽器看效果**

```
http://localhost:8080/index.html?bust=snkr2
```

Expected:
- 首頁「今日熱門」M5 卡片右下角顯示「🕒 補資料中」(若 backfill_status=pending)
- 若 M5 補完(set_id 有了)、角標消失、點下去跳本站詳情頁

- [ ] **Step 3: 拿 playwright 截圖確認**

```python
# 用 mcp__plugin_playwright_playwright__browser_take_screenshot 截圖
# 看 carousel 內每張卡角標
```

Expected screenshot: top 8 M5 卡都有「🕒 補資料中」角標

- [ ] **Step 4: Commit**

```
卡波/index.html 不在 git repo,backup 為 index.html.before-trending-backfill-label-20260524
```

```powershell
Copy-Item "C:\Users\Dong Ying\Desktop\卡波\index.html" "C:\Users\Dong Ying\Desktop\卡波\index.html.before-trending-backfill-label-20260524"
```

---

## Task 18:Stage 1 dry-run M5 端到端驗證

**Files:**
- 跑 `_test_jp_set_backfill_m5.py`(Task 9 已寫的)

- [ ] **Step 1: 強制 dry-run M5**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _test_jp_set_backfill_m5.py
```

Expected: 印出 80-120 卡的處理過程、最後 `status: done` 或 `status: failed`(若 failed 看 error_msg)

- [ ] **Step 2: 修錯誤(若有)**

常見問題:
- regex 抓不到、要回 Task 4-8 調整
- 翻譯命中率低、看 wiki_translations 數量
- pokemon-card.com 回 403、看是否要加 referer header

修完重跑直到通過。

- [ ] **Step 3: Commit fixes(若有)**

```bash
git add app/scraper/jp_set_sources.py app/scraper/jp_set_backfill.py
git commit -m "scraper: 修 M5 dry-run 過程中發現的 N 個問題"
```

---

## Task 19:Stage 2 真實寫入 M5

**Files:**
- 跑 `curl` + 後續 verify

- [ ] **Step 1: 真實觸發**

```powershell
curl.exe -s -X POST http://localhost:8000/api/admin/set-backfill/M5
```

Expected: 回 `{"set_code":"M5","job_id":N,"msg":"enqueued"}`(若 Task 11 沒先排過、否則 already)

- [ ] **Step 2: 跑 scrape_set 直接(不等排程)**

```python
# 在 _test_jp_set_backfill_m5_live.py(新建)
import asyncio
from app.scraper.jp_set_backfill import scrape_set

async def main():
    result = await scrape_set("M5", "live_test", dry_run=False)
    print(result)

asyncio.run(main())
```

跑 ~10-15 分鐘(80 卡 × 2 秒 + retry / sleep)。

- [ ] **Step 3: SQL 驗證**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
c.row_factory = sqlite3.Row
print('jp_card_list_set 新 pg:')
for r in c.execute('SELECT * FROM jp_card_list_set ORDER BY CAST(pg AS INTEGER) DESC LIMIT 3'):
    print(' ', dict(r))
print()
print('M5 (pg=954) 卡片總數:')
cnt = c.execute('SELECT COUNT(*) FROM jp_card_list WHERE set_code=\"M5\"').fetchone()[0]
print(f'  {cnt} 張(預期 80-120)')
print()
print('翻譯率(name_jp 反查 _jp_zh_translations.json 或 pokemon_dict / jp_term_dict):')
trans_cnt = c.execute(
    'SELECT COUNT(*) FROM jp_card_list jcl '
    'LEFT JOIN pokemon_dict pd ON pd.name_jp=jcl.name_jp '
    'LEFT JOIN jp_term_dict jtd ON jtd.name_jp=jcl.name_jp '
    'WHERE jcl.set_code=\"M5\" AND (pd.name_zh IS NOT NULL OR jtd.name_zh IS NOT NULL)'
).fetchone()[0]
print(f'  {trans_cnt}/{cnt} (預期 ≥80%)')
"
```

Expected:
- jp_card_list_set 新 pg=954
- M5 卡片 80-120
- 翻譯率 ≥80%

- [ ] **Step 4: Commit verify script(若還沒有)**

```bash
git add _test_jp_set_backfill_m5_live.py 2>/dev/null || true
# .gitignore 排 _* 開頭,git 不會 add(local-only),OK
```

---

## Task 20:Stage 3 端到端 SNKR 熱門 → 詳情頁

**Files:**
- 清掉 SNKR 熱門 cache、重 refresh、playwright 開瀏覽器看

- [ ] **Step 1: 清 SNKR 熱門 cache + refresh**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
c.execute('DELETE FROM snkr_hot_items')
c.commit()
print('cache cleared')
"
curl.exe -s -X POST http://localhost:8000/api/admin/snkr-hot/refresh
```

Expected: saved=30、mapped_to_db 比之前(1 → 7)應該 ≥8(M5 加進來了)

- [ ] **Step 2: 查 API 看 M5 卡 set_id 是否有了**

```powershell
curl.exe -s "http://localhost:8000/api/snkr/hot?limit=10"
```

Expected: M5 那幾張卡的 `set_id=954`、`card_number` 有值、`backfill_status=null`(因為已 done)

- [ ] **Step 3: 用 playwright 開首頁、截圖驗證**

```python
# mcp__plugin_playwright_playwright__browser_navigate http://localhost:8080/index.html?bust=stage3
# mcp__plugin_playwright_playwright__browser_take_screenshot fullPage
```

Expected: 首頁 carousel 內 M5 卡片不再有「🕒 補資料中」、點下去進詳情頁

- [ ] **Step 4: playwright 點一張 M5 卡確認跳詳情**

```python
# 評估 click on 第一個 carousel card 含 M5 keyword
# 看是否進 #/detail?set=954&card=N
```

Expected: 頁面切到 `#/detail?set=954&card=2`(或對應 cardnumber)、顯示卡名 + 卡圖

---

## Task 21:抽 10 張 M5 人工核對

**Files:**
- 跑 `_audit_m5_quality.py`(新建)生抽樣表給 user 核對

- [ ] **Step 1: 寫 audit 腳本**

```python
"""抽 10 張 M5 卡跟 pokemon-card.com / 52poke wiki 比對。"""
import sqlite3
import random

c = sqlite3.connect('cards.db')
c.row_factory = sqlite3.Row
rows = c.execute(
    "SELECT cardID, card_number, name_jp, name_zh, rarity, image_url "
    "FROM jp_card_list jcl "
    "LEFT JOIN (SELECT name_jp, name_zh FROM pokemon_dict UNION "
    "           SELECT name_jp, name_zh FROM jp_term_dict) d USING(name_jp) "
    "WHERE set_code='M5' ORDER BY RANDOM() LIMIT 10"
).fetchall()

print("| # | cardID | cn | name_jp | name_zh | rarity | URL |")
print("|---|---|---|---|---|---|---|")
for i, r in enumerate(rows, 1):
    pc_url = f"https://www.pokemon-card.com/card-search/details.php/card/{r['cardID']}/regu/all"
    print(f"| {i} | {r['cardID']} | {r['card_number']} | {r['name_jp']} | "
          f"{r['name_zh'] or '—'} | {r['rarity']} | {pc_url} |")
```

跑:`PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _audit_m5_quality.py > _audit_m5.md`

- [ ] **Step 2: User 看 _audit_m5.md 比對**

逐張對:
- 點 pokemon-card.com URL、看 jp name / rarity / 卡號是否跟 DB 一致
- 看 _jp_zh_translations.json 或 wiki 翻譯是否合理

- [ ] **Step 3: 通過門檻 ≥9/10**

若有錯、寫進 PROGRESS.md Known Pitfalls、未來改進。

---

## Task 22:M4 補漏卡測試

**Files:**
- 跑 `_test_jp_set_backfill_m4_extra.py`(Task 10 已寫的)

- [ ] **Step 1: 跑 M4 補漏卡**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _test_jp_set_backfill_m4_extra.py
```

Expected: 印 `新增 30-31 張` + `OK`

- [ ] **Step 2: SQL 驗證**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
# M4 卡數
cnt = c.execute('SELECT COUNT(*) FROM jp_card_list WHERE pg=\"953\"').fetchone()[0]
print(f'M4 (pg=953) 卡數: {cnt} (預期從 83 → 110+)')
# #84-114 是否有
new_cards = c.execute('SELECT card_number, name_jp FROM jp_card_list WHERE pg=\"953\" AND CAST(card_number AS INTEGER) > 83 ORDER BY CAST(card_number AS INTEGER)').fetchall()
print(f'M4 #84+ 卡:')
for cn, nm in new_cards[:5]:
    print(f'  #{cn} {nm}')
"
```

Expected:
- M4 卡數 ≥110
- #84+ 開始有 SAR/UR/MUR 變體

- [ ] **Step 3: Commit M4 補完進 PROGRESS.md(可選)**

```
寫個 entry 進 PROGRESS.md 「2026-05-25 M4 補漏卡完成、+30 張」
```

---

## Self-Review checklist(寫完看一遍、Task 範圍對 spec 完整)

| Spec 章節 | 對應 Task |
|---|---|
| 二、整體架構 | Task 2 骨架 + Task 11 偵測 + Task 12 排程 |
| 三、資料模型(set_backfill_jobs) | Task 1 schema |
| 三、資料模型(jp_card_list_set INSERT) | Task 9 scrape_set |
| 三、資料模型(jp_card_list INSERT) | Task 9 + Task 10 |
| 三、新 pg 分配規則 | Task 3 |
| 三、補漏卡(M4) | Task 10 |
| 四、錯誤處理(retry / consecutive_fails) | Task 9 內部邏輯 |
| 四、resumable(detect_dead_running_jobs) | Task 2 |
| 四、翻譯 5 層 fallback | Task 8 wiki + Task 9 整合 |
| 五、admin endpoints | Task 13 / 14 / 15 |
| 五、前端角標 | Task 16 + Task 17 |
| 六、4 階段驗證 | Task 18 / 19 / 20 / 21 / 22 |
| 七、常數 | Task 2 |

**全部覆蓋 ✓**

---

## 預估時間

| 階段 | Task 數 | 預估時間 |
|---|---|---|
| Stage 1 schema + 骨架 | Task 1-3 | 30-45 分鐘 |
| Stage 2 來源爬蟲 | Task 4-8 | 1.5-2 小時(regex 調整佔大宗) |
| Stage 3 主流程 | Task 9-10 | 1 小時 |
| Stage 4 偵測 + 排程 | Task 11-12 | 30 分鐘 |
| Stage 5 API | Task 13-15 | 30 分鐘 |
| Stage 6 前端 | Task 16-17 | 30 分鐘 |
| Stage 7 驗證 | Task 18-22 | 1.5-2 小時(含實際跑 M5 + M4) |
| **總計** | **22 個 task** | **5-7 小時** |

---

## 已知風險

1. **pokemon-card.com regex 可能不對**:Task 4-6 的 regex 是 spec-time 推測、實際動工要看 HTML 跑 spike。**保留 30 分鐘做這個調整 buffer**。
2. **artofpkm.com 卡圖 fallback 可能失敗**:Task 7 內 partial match 邏輯粗糙、若 set 名變動 0 命中。**fallback 用 pokemon-card.com 原圖、不阻塞主流程**。
3. **52poke wiki M5 頁可能還沒建好**:M5 是 SNKR 最熱、新發行、wiki 可能還在補。**fallback 寫 NULL、user 後續手動補**。
4. **SNKR ToS 風險仍存在**:每天爬一次 + 卡之間 2 秒、低調但不為零。
5. **cards.db 並行寫鎖**:Task 9 + Task 11 偵測 / Task 12 排程同時跑可能撞鎖。**保險用「Task 9 跑時不啟動其他 backfill」**。
