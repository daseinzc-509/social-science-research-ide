"""基于 SQLite FTS5 的中英双语全文检索引擎。

设计约束：
1. 严格保持来源可审计性（Evidence-first）：每条索引记录均显式携带
   `source_kind`（METADATA / SOURCE_BLOCK / PAPER_CARD / USER_NOTE / HUMAN_REVIEWED）
   与 `provenance`，绝不将 AI 评审或人工局部校订混同为原始 PDF 证据块。
2. 零幻觉摘录：`snippet_source` 保存原始未分词文本，搜索结果摘要直接从原文切片，
   分词列 `fts_tokens` 仅用于倒排匹配与 BM25 相关性排序。
3. 中英双语防注入查询：连续中文自动切分为单字 + 相邻双字（bigram）词元，
   任意特殊字符输入均被净化为双引号短语词元，杜绝 FTS5 语法异常。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .models import PaperCard

_LATIN_TOKEN_RE = re.compile(r"[0-9a-z]+")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_SEGMENT_RE = re.compile(
    r"[0-9A-Za-z]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+"
)

VALID_SOURCE_KINDS = frozenset({
    "METADATA",
    "SOURCE_BLOCK",
    "PAPER_CARD",
    "USER_NOTE",
    "PROJECT_NOTE",
    "HUMAN_REVIEWED",
})


def tokenize_for_fts(text: str) -> str:
    """将中英文混合学术文本转换为适合 FTS5 unicode61 索引的空格分隔词元串。"""
    if not text:
        return ""
    lowered = text.casefold()
    tokens: list[str] = []
    for match in _SEGMENT_RE.finditer(lowered):
        chunk = match.group(0)
        if _CJK_RUN_RE.fullmatch(chunk):
            # 同时产出单字与相邻双字词元，兼顾单字词（如“税”）与多字术语（如“双重差分”）
            for ch in chunk:
                tokens.append(ch)
            for idx in range(len(chunk) - 1):
                tokens.append(chunk[idx : idx + 2])
        else:
            tokens.append(chunk)
    return " ".join(tokens)


def build_fts_match_query(raw_query: str) -> str:
    """将用户任意自由输入安全转换为 FTS5 MATCH 布尔表达式，防止语法注入报错。"""
    clean = (raw_query or "").strip().casefold()
    if not clean:
        return ""
    clauses: list[str] = []
    seen: set[str] = set()
    for match in _SEGMENT_RE.finditer(clean):
        chunk = match.group(0)
        if _CJK_RUN_RE.fullmatch(chunk):
            units = [chunk] if len(chunk) == 1 else [
                chunk[idx : idx + 2] for idx in range(len(chunk) - 1)
            ]
            for unit in units:
                expr = f'"{unit}"'
                if expr not in seen:
                    seen.add(expr)
                    clauses.append(expr)
        else:
            # 拉丁与数字词元长度 >= 2 时启用前缀匹配，支持如 "identif" 命中 "identification"
            expr = f'"{chunk}"*' if len(chunk) >= 2 else f'"{chunk}"'
            if expr not in seen:
                seen.add(expr)
                clauses.append(expr)
        if len(clauses) >= 24:
            break
    return " AND ".join(clauses)


def extract_snippet(text: str, raw_query: str, *, max_chars: int = 180) -> str:
    """从原始文本中围绕首个命中词裁剪上下文摘要，不改动原文字面内容。"""
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if len(compact) <= max_chars:
        return compact

    lower_text = compact.casefold()
    anchor_pos = -1
    for match in _SEGMENT_RE.finditer((raw_query or "").strip().casefold()):
        term = match.group(0)
        pos = lower_text.find(term)
        if pos >= 0:
            anchor_pos = pos
            break
        if _CJK_RUN_RE.fullmatch(term) and len(term) >= 2:
            pos = lower_text.find(term[:2])
            if pos >= 0:
                anchor_pos = pos
                break

    if anchor_pos < 0:
        return compact[: max_chars - 1].rstrip() + "…"

    half = max(20, max_chars // 2)
    start = max(0, anchor_pos - half // 2)
    end = min(len(compact), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(compact) else ""
    return f"{prefix}{compact[start:end].strip()}{suffix}"


def fts5_available(connection: sqlite3.Connection) -> bool:
    """检测当前 SQLite 连接是否支持 FTS5 虚拟表扩展。"""
    try:
        options = {
            str(row[0]).upper()
            for row in connection.execute("PRAGMA compile_options").fetchall()
        }
        if "ENABLE_FTS5" in options:
            return True
    except sqlite3.OperationalError:
        pass
    return False


def ensure_fts_schema(connection: sqlite3.Connection) -> bool:
    """幂等创建 search_fts 虚拟表；若环境不支持 FTS5 则返回 False。"""
    if not fts5_available(connection):
        return False
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
            fts_tokens,
            doc_key UNINDEXED,
            paper_id UNINDEXED,
            source_kind UNINDEXED,
            provenance UNINDEXED,
            page_number UNINDEXED,
            snippet_source UNINDEXED,
            tokenize = 'unicode61 remove_diacritics 2'
        )
        """
    )
    return True


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def sync_paper_fts(connection: sqlite3.Connection, paper_id: str) -> int:
    """在当前连接事务内重建单篇论文的全部 FTS5 索引记录（删除论文时自动清空）。"""
    if not _table_exists(connection, "search_fts"):
        return 0

    connection.execute("DELETE FROM search_fts WHERE paper_id = ?", (paper_id,))
    paper_row = connection.execute(
        "SELECT id, original_name FROM papers WHERE id = ?",
        (paper_id,),
    ).fetchone()
    if paper_row is None:
        return 0

    entries: list[tuple[str, str, str, str, str, int | None, str]] = []

    def add_entry(
        doc_key: str,
        source_kind: str,
        provenance: str,
        page_number: int | None,
        raw_text: str,
        *,
        extra_index_text: str = "",
    ) -> None:
        clean_raw = (raw_text or "").strip()
        if not clean_raw:
            return
        combined_for_tokens = (
            f"{clean_raw} {extra_index_text.strip()}"
            if extra_index_text and extra_index_text.strip()
            else clean_raw
        )
        tokenized = tokenize_for_fts(combined_for_tokens)
        if not tokenized:
            return
        entries.append((
            tokenized,
            doc_key,
            paper_id,
            source_kind,
            provenance,
            page_number,
            clean_raw[:4000],
        ))

    # 1. 元数据（合并自动提取 Card 元数据与研究者手动覆写元数据）
    card_row = connection.execute(
        "SELECT card_json FROM paper_cards WHERE paper_id = ?",
        (paper_id,),
    ).fetchone()
    override_row = connection.execute(
        "SELECT metadata_json, source_note FROM paper_metadata_overrides WHERE paper_id = ?",
        (paper_id,),
    ).fetchone()

    card: PaperCard | None = None
    if card_row is not None:
        try:
            card = PaperCard.model_validate_json(card_row["card_json"])
        except Exception:
            card = None

    meta_dict: dict[str, Any] = card.metadata.model_dump(mode="python") if card is not None else {}
    has_manual_override = False
    override_note = ""
    if override_row is not None:
        try:
            parsed_override = json.loads(override_row["metadata_json"])
            if isinstance(parsed_override, dict) and parsed_override:
                meta_dict.update(parsed_override)
                has_manual_override = True
        except Exception:
            pass
        override_note = str(override_row["source_note"] or "").strip()

    meta_parts: list[str] = [str(paper_row["original_name"] or "")]
    for key in ("title", "journal", "doi"):
        val = meta_dict.get(key)
        if val:
            meta_parts.append(str(val))
    if meta_dict.get("year") is not None:
        meta_parts.append(str(meta_dict["year"]))
    for list_key in ("authors", "keywords"):
        items = meta_dict.get(list_key) or []
        if isinstance(items, list) and items:
            meta_parts.append(" ".join(str(x) for x in items if x))
    if meta_dict.get("abstract"):
        meta_parts.append(str(meta_dict["abstract"]))
    if override_note:
        meta_parts.append(override_note)

    add_entry(
        f"metadata:{paper_id}",
        "METADATA",
        "HUMAN_REVIEWED" if has_manual_override else "AUTHOR_STATED",
        1,
        " | ".join(part for part in meta_parts if part),
    )

    # 2. 原文块（SourceBlock，严格标识 DOCLING_SOURCE）
    block_rows = connection.execute(
        "SELECT id, page_number, text FROM source_blocks WHERE paper_id = ? ORDER BY page_number, rowid",
        (paper_id,),
    ).fetchall()
    for block in block_rows:
        add_entry(
            f"block:{block['id']}",
            "SOURCE_BLOCK",
            "DOCLING_SOURCE",
            int(block["page_number"]),
            str(block["text"]),
        )

    # 3. Paper Card 提取声明与方法学审读（严格保留各自 Provenance）
    if card is not None:
        claim_groups = [
            *card.basic_facts,
            *card.analysis,
            *card.limitations,
            *([card.reading_recommendation] if card.reading_recommendation is not None else []),
        ]
        for idx, claim in enumerate(claim_groups):
            first_page = claim.evidence[0].page_number if claim.evidence else None
            text_bits = [claim.field_name, claim.statement]
            if claim.rationale:
                text_bits.append(claim.rationale)
            evidence_quotes = " ".join(ref.quote for ref in claim.evidence if ref.quote)
            add_entry(
                f"card:{paper_id}:{claim.claim_id or idx}",
                "PAPER_CARD",
                str(claim.provenance.value),
                first_page,
                " — ".join(bit for bit in text_bits if bit),
                extra_index_text=evidence_quotes,
            )
        if card.study_profile is not None:
            sp = card.study_profile
            profile_bits = [
                str(sp.study_type.value),
                sp.method or "",
                sp.identification_strategy or "",
                sp.population or "",
                sp.data_source or "",
                sp.measurement or "",
                sp.rationale or "",
            ]
            add_entry(
                f"profile:{paper_id}",
                "PAPER_CARD",
                "AI_INFERRED",
                None,
                " | ".join(bit for bit in profile_bits if bit),
            )

    # 4. 用户个人笔记（USER_NOTE）
    note_cols = {
        str(r["name"])
        for r in connection.execute("PRAGMA table_info(user_notes)").fetchall()
    }
    has_rich_note = {"title", "page_number"}.issubset(note_cols)
    note_rows = connection.execute(
        (
            "SELECT id, title, note, page_number FROM user_notes WHERE paper_id = ? ORDER BY rowid"
            if has_rich_note
            else "SELECT id, note FROM user_notes WHERE paper_id = ? ORDER BY rowid"
        ),
        (paper_id,),
    ).fetchall()
    for note in note_rows:
        note_title = str(note["title"] or "").strip() if has_rich_note else ""
        note_body = str(note["note"] or "").strip()
        note_page = (
            int(note["page_number"])
            if has_rich_note and note["page_number"] is not None
            else None
        )
        combined_note = f"{note_title} — {note_body}" if note_title else note_body
        add_entry(
            f"note:{note['id']}",
            "USER_NOTE",
            "USER_NOTE",
            note_page,
            combined_note,
        )

    # 5. 当前有效的人工局部复核记录（HUMAN_REVIEWED，仅限未归档且未撤销版本）
    if _table_exists(connection, "region_reviews"):
        review_cols = {
            str(r["name"])
            for r in connection.execute("PRAGMA table_info(region_reviews)").fetchall()
        }
        where_active = ""
        if {"superseded_at", "revoked_at"}.issubset(review_cols):
            where_active = " AND superseded_at IS NULL AND revoked_at IS NULL"
        review_rows = connection.execute(
            f"SELECT id, page_number, corrected_text, researcher_note FROM region_reviews "
            f"WHERE paper_id = ?{where_active} ORDER BY page_number, rowid",
            (paper_id,),
        ).fetchall()
        for rev in review_rows:
            combined = " — ".join(
                part
                for part in (
                    str(rev["corrected_text"] or "").strip(),
                    str(rev["researcher_note"] or "").strip(),
                )
                if part
            )
            add_entry(
                f"review:{rev['id']}",
                "HUMAN_REVIEWED",
                "HUMAN_REVIEWED",
                int(rev["page_number"]),
                combined,
            )

    # 6. Claim 单条人工审定与修正记录（HUMAN_REVIEWED）
    if _table_exists(connection, "claim_reviews"):
        crev_rows = connection.execute(
            """
            SELECT id, claim_id, user_status, corrected_statement, note
            FROM claim_reviews
            WHERE paper_id = ?
            ORDER BY updated_at DESC, rowid DESC
            """,
            (paper_id,),
        ).fetchall()
        for crev in crev_rows:
            combined_crev = " — ".join(
                part
                for part in (
                    str(crev["corrected_statement"] or "").strip(),
                    str(crev["note"] or "").strip(),
                )
                if part
            )
            if combined_crev:
                add_entry(
                    f"claim_review:{crev['id']}",
                    "HUMAN_REVIEWED",
                    "HUMAN_REVIEWED",
                    None,
                    combined_crev,
                )

    if entries:
        connection.executemany(
            """
            INSERT INTO search_fts (
                fts_tokens, doc_key, paper_id, source_kind, provenance, page_number, snippet_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            entries,
        )
    return len(entries)


def sync_project_notes_fts(connection: sqlite3.Connection, project_id: str) -> int:
    """在当前连接事务内重建单个研究专题的专题笔记 FTS5 索引（删除专题时自动清空）。"""
    if not _table_exists(connection, "search_fts"):
        return 0
    pseudo_paper_id = f"project:{project_id}"
    connection.execute("DELETE FROM search_fts WHERE paper_id = ?", (pseudo_paper_id,))
    if not _table_exists(connection, "research_projects") or not _table_exists(connection, "project_notes"):
        return 0
    proj_row = connection.execute(
        "SELECT id, name FROM research_projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if proj_row is None:
        return 0
    proj_name = str(proj_row["name"] or "").strip()
    rows = connection.execute(
        "SELECT id, title, note FROM project_notes WHERE project_id = ? ORDER BY rowid",
        (project_id,),
    ).fetchall()
    entries: list[tuple[str, str, str, str, str, int | None, str]] = []
    for row in rows:
        title = str(row["title"] or "").strip()
        body = str(row["note"] or "").strip()
        if not body:
            continue
        raw = f"{title} — {body}" if title else body
        tokenized = tokenize_for_fts(f"{raw} {proj_name}" if proj_name else raw)
        if not tokenized:
            continue
        entries.append((
            tokenized,
            f"project_note:{project_id}:{row['id']}",
            pseudo_paper_id,
            "PROJECT_NOTE",
            "USER_NOTE",
            None,
            raw[:4000],
        ))
    if entries:
        connection.executemany(
            """
            INSERT INTO search_fts (
                fts_tokens, doc_key, paper_id, source_kind, provenance, page_number, snippet_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            entries,
        )
    return len(entries)


def rebuild_all_fts(connection: sqlite3.Connection) -> int:
    """全量重建当前数据库内所有论文与专题研究笔记的 FTS5 索引。"""
    if not ensure_fts_schema(connection):
        return 0
    connection.execute("DELETE FROM search_fts")
    paper_ids = [
        str(row["id"])
        for row in connection.execute("SELECT id FROM papers ORDER BY created_at").fetchall()
    ]
    total = 0
    for pid in paper_ids:
        total += sync_paper_fts(connection, pid)
    if _table_exists(connection, "research_projects") and _table_exists(connection, "project_notes"):
        proj_ids = [
            str(row["id"])
            for row in connection.execute("SELECT id FROM research_projects ORDER BY created_at").fetchall()
        ]
        for proj_id in proj_ids:
            total += sync_project_notes_fts(connection, proj_id)
    return total


def _resolve_paper_titles(connection: sqlite3.Connection, paper_ids: set[str]) -> dict[str, str]:
    if not paper_ids:
        return {}
    titles: dict[str, str] = {}
    project_ids = {pid[len("project:"):] for pid in paper_ids if pid.startswith("project:")}
    real_paper_ids = {pid for pid in paper_ids if not pid.startswith("project:")}

    if project_ids and _table_exists(connection, "research_projects"):
        placeholders = ",".join("?" for _ in project_ids)
        for row in connection.execute(
            f"SELECT id, name FROM research_projects WHERE id IN ({placeholders})",
            tuple(project_ids),
        ).fetchall():
            titles[f"project:{row['id']}"] = f"专题 · {row['name']}"

    if not real_paper_ids:
        return titles

    placeholders = ",".join("?" for _ in real_paper_ids)
    params = tuple(real_paper_ids)
    for row in connection.execute(
        f"SELECT id, original_name FROM papers WHERE id IN ({placeholders})",
        params,
    ).fetchall():
        titles[str(row["id"])] = str(row["original_name"])

    for row in connection.execute(
        f"SELECT paper_id, card_json FROM paper_cards WHERE paper_id IN ({placeholders})",
        params,
    ).fetchall():
        pid = str(row["paper_id"])
        try:
            data = json.loads(row["card_json"])
            title = ((data.get("metadata") or {}).get("title") or "").strip()
            if title:
                titles[pid] = title
        except Exception:
            continue

    for row in connection.execute(
        f"SELECT paper_id, metadata_json FROM paper_metadata_overrides WHERE paper_id IN ({placeholders})",
        params,
    ).fetchall():
        pid = str(row["paper_id"])
        try:
            data = json.loads(row["metadata_json"])
            title = (data.get("title") or "").strip() if isinstance(data, dict) else ""
            if title:
                titles[pid] = title
        except Exception:
            continue
    return titles


def search_documents(
    connection: sqlite3.Connection,
    query: str,
    *,
    paper_ids: set[str] | None = None,
    project_id: str | None = None,
    source_kinds: set[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """执行跨文献全文检索并返回按相关性排序的可溯源命中片段（含专题研究笔记）。"""
    capped = max(1, min(int(limit), 200))
    effective_paper_ids: set[str] | None = set(paper_ids) if paper_ids is not None else None
    if project_id:
        if effective_paper_ids is None:
            effective_paper_ids = set()
        effective_paper_ids.add(f"project:{project_id}")
    if effective_paper_ids is not None and len(effective_paper_ids) == 0:
        return []
    match_expr = build_fts_match_query(query)
    if not match_expr:
        return []

    allowed_kinds = (
        {k.strip().upper() for k in source_kinds if k.strip().upper() in VALID_SOURCE_KINDS}
        if source_kinds
        else None
    )
    if source_kinds and not allowed_kinds:
        return []

    if _table_exists(connection, "search_fts"):
        sql = [
            """
            SELECT
                doc_key,
                paper_id,
                source_kind,
                provenance,
                page_number,
                snippet_source,
                bm25(search_fts) AS rank_score
            FROM search_fts
            WHERE search_fts MATCH ?
            """
        ]
        params: list[Any] = [match_expr]
        if effective_paper_ids is not None:
            placeholders = ",".join("?" for _ in effective_paper_ids)
            sql.append(f"AND paper_id IN ({placeholders})")
            params.extend(sorted(effective_paper_ids))
        if allowed_kinds:
            placeholders = ",".join("?" for _ in allowed_kinds)
            sql.append(f"AND source_kind IN ({placeholders})")
            params.extend(sorted(allowed_kinds))
        sql.append("ORDER BY rank_score ASC LIMIT ?")
        params.append(capped)
        rows = connection.execute("\n".join(sql), tuple(params)).fetchall()
    else:
        # 极端缺少 FTS5 编译支持时的安全 LIKE 降级路径
        if allowed_kinds is not None and "SOURCE_BLOCK" not in allowed_kinds:
            return []
        needle = f"%{(query or '').strip()}%"
        fallback_sql = [
            """
            SELECT
                'block:' || id AS doc_key,
                paper_id,
                'SOURCE_BLOCK' AS source_kind,
                'DOCLING_SOURCE' AS provenance,
                page_number,
                text AS snippet_source,
                0.0 AS rank_score
            FROM source_blocks
            WHERE text LIKE ?
            """
        ]
        fallback_params: list[Any] = [needle]
        if paper_ids is not None:
            if not paper_ids:
                return []
            placeholders = ",".join("?" for _ in paper_ids)
            fallback_sql.append(f"AND paper_id IN ({placeholders})")
            fallback_params.extend(sorted(paper_ids))
        fallback_sql.append("LIMIT ?")
        fallback_params.append(capped)
        rows = connection.execute("\n".join(fallback_sql), tuple(fallback_params)).fetchall()

    matched_pids = {str(row["paper_id"]) for row in rows}
    titles = _resolve_paper_titles(connection, matched_pids)
    hits: list[dict[str, Any]] = []
    for row in rows:
        pid = str(row["paper_id"])
        raw_page = row["page_number"]
        page_num = int(raw_page) if raw_page is not None and str(raw_page).strip() != "" else None
        # SQLite bm25() 越小越相关（通常为负数），取负转为直观正分
        raw_rank = float(row["rank_score"] or 0.0)
        score = round(-raw_rank, 6) if raw_rank != 0.0 else 0.0
        hits.append({
            "doc_key": str(row["doc_key"]),
            "paper_id": pid,
            "paper_title": titles.get(pid, pid),
            "source_kind": str(row["source_kind"]),
            "provenance": str(row["provenance"]),
            "page_number": page_num,
            "snippet": extract_snippet(str(row["snippet_source"]), query),
            "score": score,
        })
    return hits
