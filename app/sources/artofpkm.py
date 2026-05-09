"""artofpkm secondary source — Stage 2（唯讀，不寫 DB）"""
from __future__ import annotations
import re
import sys
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .base import SecondarySource, CardRecord

BASE = "https://www.artofpkm.com"
UA = "cardpool-importer/0.1"
SLEEP_SEC = 1.5
TIMEOUT = 20
MAX_RETRIES = 2
RETRY_SLEEP = 3

LISTING_RE = re.compile(
    r'data-lightbox-url="/sets/(\d+)/card/(\d+)"',
    re.DOTALL,
)

NEXT_BATCH_RE = re.compile(
    r'<turbo-frame[^>]*id="card_batch_\d+"[^>]*src="([^"]+)"'
)

MAX_BATCHES = 30

CARD_NUMBER_RE = re.compile(r'\b([A-Z0-9]{1,4}\s*/\s*[A-Z0-9]{1,4})\b')
JP_CHAR_RE = re.compile(r'[぀-ゟ゠-ヿ一-鿿　-〿]')


class ArtofpkmSource(SecondarySource):
    name = "artofpkm"
    provided_fields = {"name_jp", "name"}

    def __init__(self):
        self._client: Optional[httpx.Client] = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": UA, "Accept": "text/html"},
                timeout=TIMEOUT,
                follow_redirects=True,
            )
        return self._client

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None

    def _fetch(self, url: str) -> Optional[str]:
        client = self._get_client()
        for attempt in range(MAX_RETRIES + 1):
            try:
                r = client.get(url)
                if r.status_code == 200:
                    r.encoding = "utf-8"
                    return r.text
            except httpx.HTTPError:
                pass
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP)
        return None

    def _collect_all_listing_html(self, art_set_id: int) -> str:
        """抓主 listing 頁 + 所有 lazy-loaded turbo-frame batches，串起整段 HTML。

        artofpkm 用 Rails Turbo Frame，主頁只渲染前 100 張，剩下藏在
        <turbo-frame id="card_batch_N" src="/sets/{id}/card_batches?offset=N">
        裡的 lazy-load fragment。沿著 next-frame 連結爬到底為止。
        """
        first_url = f"{BASE}/sets/{art_set_id}/cards"
        first_html = self._fetch(first_url)
        if first_html is None:
            raise RuntimeError(f"failed to fetch listing: {first_url}")

        chunks = [first_html]
        next_match = NEXT_BATCH_RE.search(first_html)
        batch_count = 0
        while next_match:
            if batch_count >= MAX_BATCHES:
                raise RuntimeError(
                    f"artofpkm listing for set {art_set_id} exceeded {MAX_BATCHES} batches "
                    f"({MAX_BATCHES * 100} cards). Either set is unexpectedly large or "
                    f"turbo-frame next-link detection is broken. "
                    f"Bump MAX_BATCHES if set is genuinely larger."
                )
            time.sleep(SLEEP_SEC)
            next_url = f"{BASE}{next_match.group(1)}"
            fragment = self._fetch(next_url)
            if fragment is None:
                raise RuntimeError(f"failed to fetch listing batch: {next_url}")
            chunks.append(fragment)
            batch_count += 1
            next_match = NEXT_BATCH_RE.search(fragment)
        return "".join(chunks)

    def _parse_listing(self, html: str, art_set_id: int) -> list[int]:
        seqs: set[int] = set()
        for m in LISTING_RE.finditer(html):
            sid = int(m.group(1))
            if sid != art_set_id:
                continue
            seqs.add(int(m.group(2)))
        return sorted(seqs)

    def _parse_card_page(self, html: str) -> dict:
        soup = BeautifulSoup(html, "lxml")
        out = {"card_number": None, "name_jp": None, "name": None}

        # card_number: 優先 <title>，fallback 掃 h1/h2/h3
        if soup.title and soup.title.string:
            m = CARD_NUMBER_RE.search(soup.title.string)
            if m:
                out["card_number"] = m.group(1).replace(" ", "")

        # name (英文) / name_jp from h1/h2/h3，順便 fallback 補 card_number
        for tag in soup.find_all(["h1", "h2", "h3"]):
            text = tag.get_text(strip=True)
            if not text:
                continue
            if out["card_number"] is None:
                m = CARD_NUMBER_RE.search(text)
                if m:
                    out["card_number"] = m.group(1).replace(" ", "")
                    continue
            if out["name_jp"] is None and JP_CHAR_RE.search(text):
                out["name_jp"] = text
                continue
            if out["name"] is None and not JP_CHAR_RE.search(text) and any(c.isalpha() for c in text):
                if not CARD_NUMBER_RE.search(text):
                    out["name"] = text
        return out

    def fetch_set(
        self,
        source_set_id: str,
        max_cards: Optional[int] = None,
    ) -> list[CardRecord]:
        try:
            art_id = int(source_set_id)
        except ValueError:
            raise ValueError(f"artofpkm source_set_id must be numeric, got: {source_set_id!r}")

        listing_html = self._collect_all_listing_html(art_id)
        time.sleep(SLEEP_SEC)

        seqs = self._parse_listing(listing_html, art_id)
        if max_cards is not None:
            seqs = seqs[:max_cards]

        records: list[CardRecord] = []
        skipped = 0
        for i, seq in enumerate(seqs):
            detail_url = f"{BASE}/sets/{art_id}/card/{seq}"
            detail_html = self._fetch(detail_url)
            time.sleep(SLEEP_SEC)
            if detail_html is None:
                print(f"[WARN] seq={seq} fetch failed, skipped", file=sys.stderr)
                skipped += 1
                continue
            parsed = self._parse_card_page(detail_html)
            if parsed["card_number"] is None:
                print(f"[WARN] seq={seq} card_number not parsed in heading, skipped", file=sys.stderr)
                skipped += 1
                continue
            fields = {}
            if parsed["name_jp"]:
                fields["name_jp"] = parsed["name_jp"]
            if parsed["name"]:
                fields["name"] = parsed["name"]
            records.append(CardRecord(
                card_number=parsed["card_number"],
                fields=fields,
                source_meta={"art_set_id": art_id, "seq": seq},
            ))
            if (i + 1) % 50 == 0:
                print(f"[progress] {i+1}/{len(seqs)} fetched", file=sys.stderr)

        print(
            f"[INFO] fetched {len(records)} records, skipped {skipped} "
            f"(card_number unparseable or fetch failed)",
            file=sys.stderr,
        )
        return records
