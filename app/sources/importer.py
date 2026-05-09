"""Stage 3: secondary source importer — dry-run / apply 兩段式。

主流程：
1. source.fetch_set(source_set_id) 拿到 list[CardRecord]
2. 對每張卡每個欄位套 FIELD_PRIORITY 做決策（will_update / no_change / conflict / unmatched / source_missing_field）
3. dry_run=True 只回傳 ImportReport，不寫 DB
4. dry_run=False 在單一 transaction 內 UPDATE card_list + UPSERT card_field_sources
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from .base import SecondarySource, CardRecord
from .priority import FIELD_PRIORITY


# 寫進 card_list 的欄位白名單（field_name 與 column 名 1:1 同名，仍二次驗證防誤拼）
ALLOWED_CARD_LIST_COLS = {"name", "name_jp", "name_zh", "rarity", "image_url"}


def _normalize_card_number(raw: str) -> str:
    """artofpkm 'NNN/TTT' → 跟 card_list 比對的 key。

    '001/070' → '1'    '1/070' → '1'    '042/172' → '42'    'TG1/172' → 'TG1'
    規則：拆 '/' 取左半；左半純數字 → int() 去前導零；非純數字 → 原樣保留。
    """
    left = raw.split("/", 1)[0]
    return str(int(left)) if left.isdigit() else left


def _normalize_for_compare(s: Optional[str]) -> Optional[str]:
    """僅 normalize curly apostrophe（U+2019）→ straight（U+0027），用於值比對。
    寫入時保留原值，不做空白/大小寫/NFKC。"""
    return None if s is None else s.replace("’", "'")


def is_higher_priority(source_a: str, source_b: str, field_name: str) -> bool:
    """source_a 是否比 source_b 高優先（更該勝出）。
    FIELD_PRIORITY list 中 index 較小 = 較高優先（manual=0 最高）。
    未知 source → index() 自然 raise ValueError。"""
    priority_list = FIELD_PRIORITY[field_name]
    return priority_list.index(source_a) < priority_list.index(source_b)


@dataclass
class FieldUpdate:
    card_id: int
    card_number: str          # 已 normalize 過的 join key（非 source 原始）
    field_name: str
    old_value: Optional[str]
    old_source: Optional[str]
    new_value: str
    new_source: str


@dataclass
class FieldConflict:
    card_id: int
    card_number: str
    field_name: str
    existing_value: str
    existing_source: str
    new_value: str
    new_source: str


@dataclass
class ImportReport:
    source_name: str
    target_set_id: str
    provided_fields: list[str]                                # sorted，invariant 用
    fetched: int = 0
    matched: int = 0
    unmatched_source_card_numbers: list[str] = field(default_factory=list)
    will_update: list[FieldUpdate] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)
    no_change: int = 0
    source_missing_field: int = 0

    def _invariant_lhs(self) -> int:
        return self.fetched * len(self.provided_fields)

    def _invariant_rhs(self) -> int:
        return (
            len(self.will_update)
            + len(self.conflicts)
            + self.no_change
            + self.source_missing_field
            + len(self.unmatched_source_card_numbers) * len(self.provided_fields)
        )

    def summary(self) -> str:
        lhs = self._invariant_lhs()
        rhs = self._invariant_rhs()
        ok = "[OK]" if lhs == rhs else "[INVARIANT VIOLATED]"
        return (
            f"=== ImportReport (source={self.source_name}, target_set={self.target_set_id}) ===\n"
            f"  provided_fields = {self.provided_fields}\n"
            f"  fetched = {self.fetched}\n"
            f"  matched = {self.matched}\n"
            f"  unmatched_records = {len(self.unmatched_source_card_numbers)}\n"
            f"  will_update = {len(self.will_update)}\n"
            f"  conflicts = {len(self.conflicts)}\n"
            f"  no_change = {self.no_change}\n"
            f"  source_missing_field = {self.source_missing_field}\n"
            f"  invariant: {self.fetched} * {len(self.provided_fields)} = {lhs} ?= "
            f"{len(self.will_update)} + {len(self.conflicts)} + {self.no_change} + "
            f"{self.source_missing_field} + {len(self.unmatched_source_card_numbers)} * "
            f"{len(self.provided_fields)} = {rhs}   {ok}"
        )

    def sample_diff(self, n: int = 10) -> str:
        if not self.will_update:
            return "(no will_update entries)"
        lines = [f"--- sample_diff (first {min(n, len(self.will_update))} of {len(self.will_update)} will_update) ---"]
        for upd in self.will_update[:n]:
            old_repr = "(null)" if upd.old_value is None else repr(upd.old_value)
            old_src = upd.old_source or "(none)"
            lines.append(
                f"  card#{upd.card_number:>5}  {upd.field_name:<8}  "
                f"{old_src:>11} {old_repr} → {upd.new_source} {upd.new_value!r}"
            )
        return "\n".join(lines)


def import_from_source(
    source: SecondarySource,
    source_set_id: str,
    target_set_id: str,
    db_path: str,
    dry_run: bool = True,
    records: Optional[list[CardRecord]] = None,
) -> ImportReport:
    """Stage 3 主入口。

    records: 可選預抓資料（dry-run + apply 共用同一份，不重跑網路）。
             None 時會呼叫 source.fetch_set(source_set_id)。
    dry_run=True：純讀 + 計算決策，回傳 report；DB 不被寫入。
    dry_run=False：先計算決策、再在單一 transaction 內 UPDATE + UPSERT；
                   任何 exception → rollback + raise。
    """
    if records is None:
        records = source.fetch_set(source_set_id)

    provided = sorted(source.provided_fields)
    report = ImportReport(
        source_name=source.name,
        target_set_id=target_set_id,
        provided_fields=provided,
        fetched=len(records),
    )

    # 防呆：source 的所有 provided_fields 必須都在 FIELD_PRIORITY，且 source.name 在 list 內
    for f in provided:
        if f not in FIELD_PRIORITY:
            raise ValueError(f"FIELD_PRIORITY missing entry for field {f!r}")
        if source.name not in FIELD_PRIORITY[f]:
            raise ValueError(
                f"source {source.name!r} not in FIELD_PRIORITY[{f!r}] = "
                f"{FIELD_PRIORITY[f]}"
            )

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.cursor()

        # 1. 載入目標 set 全部卡（join key + card_list 既有值）
        cur.execute(
            "SELECT id, card_number, name, name_jp, name_zh "
            "FROM card_list WHERE set_id = ?",
            (target_set_id,),
        )
        cards_by_number: dict[str, tuple[int, dict[str, Optional[str]]]] = {}
        for cid, cnum, cl_name, cl_jp, cl_zh in cur.fetchall():
            cards_by_number[cnum] = (
                cid,
                {"name": cl_name, "name_jp": cl_jp, "name_zh": cl_zh},
            )

        # 2. 載入既有 cfs row（這批 card_id）
        existing_cfs: dict[tuple[int, str], tuple[str, str]] = {}
        if cards_by_number:
            card_ids = [cid for cid, _ in cards_by_number.values()]
            placeholders = ",".join("?" * len(card_ids))
            cur.execute(
                f"SELECT card_id, field_name, source_name, value "
                f"FROM card_field_sources WHERE card_id IN ({placeholders})",
                card_ids,
            )
            for cid, fname, sname, val in cur.fetchall():
                existing_cfs[(cid, fname)] = (sname, val)

        # 3. 跑決策樹
        pending_card_list: list[tuple[int, str, str]] = []  # (card_id, col, val)
        pending_cfs: list[tuple[int, str, str, str]] = []   # (card_id, field, source, val)

        for rec in records:
            join_key = _normalize_card_number(rec.card_number)
            if join_key not in cards_by_number:
                # 分支 A: unmatched
                report.unmatched_source_card_numbers.append(rec.card_number)
                continue
            report.matched += 1
            card_id, cl_values = cards_by_number[join_key]

            for field_name in provided:
                new_val = rec.fields.get(field_name)
                if new_val is None or new_val == "":
                    # 分支 B: source_missing_field
                    report.source_missing_field += 1
                    continue

                # 取既有狀態
                cfs_entry = existing_cfs.get((card_id, field_name))
                cl_val = cl_values.get(field_name)
                if cfs_entry is not None:
                    existing_source, existing_value = cfs_entry
                elif cl_val is not None:
                    # 「card_list 該欄位有值，是舊 pokellector 留的」→ 合成
                    existing_source = "pokellector"
                    existing_value = cl_val
                else:
                    existing_source = None
                    existing_value = None

                # 分支 C: 兩邊都空
                if existing_value is None:
                    report.will_update.append(FieldUpdate(
                        card_id=card_id, card_number=join_key, field_name=field_name,
                        old_value=None, old_source=None,
                        new_value=new_val, new_source=source.name,
                    ))
                    pending_card_list.append((card_id, field_name, new_val))
                    pending_cfs.append((card_id, field_name, source.name, new_val))
                    continue

                # 既有有值 → 比 priority（unknown source 會在這裡 raise ValueError）
                if is_higher_priority(existing_source, source.name, field_name):
                    # 分支 D: 既有高優先，保留
                    report.no_change += 1
                elif is_higher_priority(source.name, existing_source, field_name):
                    # 分支 E: 新 source 高優先，蓋掉
                    report.will_update.append(FieldUpdate(
                        card_id=card_id, card_number=join_key, field_name=field_name,
                        old_value=existing_value, old_source=existing_source,
                        new_value=new_val, new_source=source.name,
                    ))
                    pending_card_list.append((card_id, field_name, new_val))
                    pending_cfs.append((card_id, field_name, source.name, new_val))
                else:
                    # 同 priority（也包含 source.name == existing_source）
                    if _normalize_for_compare(existing_value) == _normalize_for_compare(new_val):
                        # 分支 F: 同 pri 同值
                        report.no_change += 1
                    else:
                        # 分支 G: 同 pri 不同值，conflict（不寫）
                        report.conflicts.append(FieldConflict(
                            card_id=card_id, card_number=join_key, field_name=field_name,
                            existing_value=existing_value, existing_source=existing_source,
                            new_value=new_val, new_source=source.name,
                        ))

        # 4. 寫入（dry_run 跳過）
        if not dry_run:
            try:
                cur.execute("BEGIN")
                for cid, col, val in pending_card_list:
                    if col not in ALLOWED_CARD_LIST_COLS:
                        raise ValueError(f"refusing to UPDATE unwhitelisted column {col!r}")
                    cur.execute(
                        f"UPDATE card_list SET {col} = ? WHERE id = ?",
                        (val, cid),
                    )
                for cid, fname, sname, val in pending_cfs:
                    cur.execute(
                        """
                        INSERT INTO card_field_sources (card_id, field_name, source_name, value)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(card_id, field_name) DO UPDATE SET
                            source_name = excluded.source_name,
                            value       = excluded.value,
                            updated_at  = CURRENT_TIMESTAMP
                        """,
                        (cid, fname, sname, val),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return report
    finally:
        conn.close()
