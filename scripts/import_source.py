"""Stage 3 importer CLI — dry-run / --apply 兩段式。

usage:
  py scripts\\import_source.py --source artofpkm --source-set-id 581 \\
      --target-set-id jp-Start-Deck-100-Battle-Collection [--apply] [--limit N]

預設 dry-run（不加 --apply）。--apply 會先跑 dry-run、印報告、要求 stdin 'yes' 才寫入。
--limit N 開發測試用：只 fetch 前 N 張子頁（沿用 fetch_set 的 max_cards）。
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

# Windows console UTF-8（避免 cp950 印日文炸 UnicodeEncodeError）
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 讓 scripts/ 能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources.artofpkm import ArtofpkmSource  # noqa: E402
from app.sources.importer import import_from_source  # noqa: E402

SOURCES = {
    "artofpkm": ArtofpkmSource,
}

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cards.db",
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=SOURCES.keys())
    ap.add_argument("--source-set-id", required=True)
    ap.add_argument("--target-set-id", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="實際寫入 DB（預設 dry-run）；會先跑一次 dry-run 並要求確認")
    ap.add_argument("--limit", type=int, default=None,
                    help="只 fetch 前 N 張（開發測試用）")
    args = ap.parse_args()

    src = SOURCES[args.source]()
    try:
        print(f"[fetch] source={args.source} set={args.source_set_id} limit={args.limit}",
              file=sys.stderr)
        records = src.fetch_set(args.source_set_id, max_cards=args.limit)
    finally:
        src.close()
    print(f"[fetch] got {len(records)} records", file=sys.stderr)

    # 第一次：dry-run
    report = import_from_source(
        source=src,
        source_set_id=args.source_set_id,
        target_set_id=args.target_set_id,
        db_path=DB_PATH,
        dry_run=True,
        records=records,
    )
    print(report.summary())
    print()
    print(report.sample_diff(10))

    if not args.apply:
        return 0

    # --apply：要求人工確認
    print()
    print(f"[apply] about to write {len(report.will_update)} card_list updates "
          f"+ {len(report.will_update)} cfs upserts in a single transaction.")
    print("[apply] type 'yes' to proceed:")
    confirm = sys.stdin.readline().strip().lower()
    if confirm != "yes":
        print("[apply] aborted (input was not 'yes')")
        return 1

    # 第二次：真寫入（共用同一份 records）
    report2 = import_from_source(
        source=src,
        source_set_id=args.source_set_id,
        target_set_id=args.target_set_id,
        db_path=DB_PATH,
        dry_run=False,
        records=records,
    )
    print(f"[apply] wrote {len(report2.will_update)} card_list rows "
          f"+ {len(report2.will_update)} card_field_sources upserts")
    if report2.conflicts:
        print(f"[apply] WARNING: {len(report2.conflicts)} conflicts were skipped (not written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
