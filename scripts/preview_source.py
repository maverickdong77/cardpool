"""Preview a secondary source — 唯讀，不寫 DB。

usage:
  py scripts\\preview_source.py --source artofpkm --source-set-id 581 --limit 5
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# Windows console UTF-8（避免 cp950 印日文炸 UnicodeEncodeError）
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 讓 scripts/ 能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources.artofpkm import ArtofpkmSource  # noqa: E402


SOURCES = {
    "artofpkm": ArtofpkmSource,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=SOURCES.keys())
    ap.add_argument("--source-set-id", required=True)
    ap.add_argument("--limit", type=int, default=None,
                    help="採樣模式：只 fetch 前 N 張子頁；不給就全抓")
    args = ap.parse_args()

    src = SOURCES[args.source]()
    try:
        records = src.fetch_set(args.source_set_id, max_cards=args.limit)
    finally:
        src.close()

    print(f"\n[source={args.source} set={args.source_set_id}] 抓到 {len(records)} 張")
    print(f"provided_fields = {sorted(src.provided_fields)}")
    print()
    for i, rec in enumerate(records, 1):
        print(f"#{i}  card_number={rec.card_number}")
        for k in sorted(rec.fields.keys()):
            print(f"     {k} = {rec.fields[k]}")
        print(f"     source_meta keys = {sorted(rec.source_meta.keys())}")


if __name__ == "__main__":
    main()
