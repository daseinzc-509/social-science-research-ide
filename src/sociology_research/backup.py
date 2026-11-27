"""研究库安全备份（SQLite Online Backup .zip）、只读完整性校验与离线原子恢复。

安全边界：
1. 仅备份 `data_dir` 下的 `research.sqlite3`（经 `sqlite3.backup()` 生成的 WAL 一致性快照）
   及可选 `papers/*.pdf`；绝不读取或导出 `user_config_dir()`、DPAPI、Keychain、`.env`
   或任何 AI API Key / 会话令牌；
2. 打包使用磁盘流式临时文件，不把大体积 PDF 装进内存；PDF 使用 `ZIP_STORED` 零无谓重压，
   SQLite 数据库使用 `ZIP_DEFLATED`；遍历 `papers/` 时强制跳过符号链接并严格校对目录边界；
3. 校验器强制阻断 zip-slip 路径穿越、校验 SHA-256 与 SQLite `PRAGMA integrity_check`；
4. 恢复仅在离线 CLI 停服状态下执行，强制先校验，非空目录默认拒绝覆盖（`replace=True` 时先保留 `.bak`），
   并事务性重写 `papers.stored_path` 以无缝支持跨设备/跨盘符迁移。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .db import open_sqlite_connection

BACKUP_FORMAT_VERSION = 1
MANIFEST_NAME = "backup-manifest.json"
DATABASE_ARCHIVE_NAME = "research.sqlite3"
_MAX_ZIP_ENTRIES = 50_000
_CHUNK_BYTES = 1024 * 1024  # 1 MiB 流式分块

_TRACKED_TABLES: tuple[str, ...] = (
    "papers",
    "paper_cards",
    "user_notes",
    "claim_reviews",
    "research_projects",
    "project_papers",
    "project_notes",
    "project_syntheses",
    "region_reviews",
    "review_reanalyses",
    "reference_entries",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_zip_member(name: str) -> PurePosixPath:
    if not name or "\\" in name or name.startswith("/") or "\x00" in name:
        raise ValueError(f"Unsafe archive entry path: {name!r}")
    posix = PurePosixPath(name)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise ValueError(f"Archive entry escapes target root: {name!r}")
    return posix


def _table_counts(database_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with closing(sqlite3.connect(database_path)) as conn:
        existing = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for table in _TRACKED_TABLES:
            if table in existing:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            else:
                counts[table] = 0
    return counts


def _sqlite_integrity_errors(database_path: Path) -> list[str]:
    with closing(sqlite3.connect(database_path)) as conn:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    messages = [str(row[0]) for row in rows if row]
    if messages == ["ok"]:
        return []
    return messages or ["SQLite integrity_check returned empty output"]


def create_backup_archive(
    data_dir: Path,
    output_zip: Path,
    *,
    include_pdfs: bool = False,
) -> dict[str, Any]:
    """创建包含 WAL 一致性 SQLite 快照与校验清单的 `.zip` 备份包（绝不含凭据）。"""
    source_root = Path(data_dir).expanduser().resolve()
    source_db = source_root / "research.sqlite3"
    if not source_db.is_file():
        raise FileNotFoundError(f"Research database not found: {source_db}")

    target_zip = Path(output_zip).expanduser().resolve()
    target_zip.parent.mkdir(parents=True, exist_ok=True)

    staging_dir = Path(tempfile.mkdtemp(prefix="sra-backup-stage-"))
    try:
        snapshot_db = staging_dir / DATABASE_ARCHIVE_NAME
        # 使用 SQLite Online Backup API 导出一致性快照，自动合并 WAL 日志且不阻塞正在读写的桌面端
        with closing(open_sqlite_connection(source_db)) as src_conn:
            with closing(sqlite3.connect(snapshot_db)) as dst_conn:
                src_conn.backup(dst_conn)
                dst_conn.commit()

        integrity_errors = _sqlite_integrity_errors(snapshot_db)
        if integrity_errors:
            raise RuntimeError(
                "Backup aborted because SQLite snapshot failed integrity_check: "
                + "; ".join(integrity_errors[:3])
            )

        db_sha256 = _sha256_file(snapshot_db)
        db_bytes = snapshot_db.stat().st_size
        counts = _table_counts(snapshot_db)

        pdf_entries: list[tuple[Path, str, int, str]] = []
        skipped_symlinks = 0
        papers_root = source_root / "papers"
        if include_pdfs and papers_root.is_dir() and not papers_root.is_symlink():
            papers_resolved = papers_root.resolve()
            for dirpath, dirnames, filenames in os.walk(papers_resolved, followlinks=False):
                current_dir = Path(dirpath)
                kept_dirs: list[str] = []
                for dirname in sorted(dirnames):
                    candidate_dir = current_dir / dirname
                    if candidate_dir.is_symlink():
                        skipped_symlinks += 1
                        continue
                    kept_dirs.append(dirname)
                dirnames[:] = kept_dirs

                for filename in sorted(filenames):
                    item = current_dir / filename
                    if item.is_symlink():
                        skipped_symlinks += 1
                        continue
                    if item.suffix.lower() != ".pdf":
                        continue
                    resolved = item.resolve()
                    if not resolved.is_relative_to(papers_resolved) or not resolved.is_file():
                        continue
                    rel_posix = f"papers/{resolved.relative_to(papers_resolved).as_posix()}"
                    pdf_entries.append((resolved, rel_posix, resolved.stat().st_size, _sha256_file(resolved)))

        now = datetime.now(timezone.utc).isoformat()
        manifest: dict[str, Any] = {
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": now,
            "include_pdfs": bool(include_pdfs),
            "contains_secrets": False,
            "database": {
                "filename": DATABASE_ARCHIVE_NAME,
                "bytes": db_bytes,
                "sha256": db_sha256,
            },
            "counts": counts,
            "pdf_count": len(pdf_entries),
            "pdf_bytes": sum(item[2] for item in pdf_entries),
            "pdf_files": [
                {"path": rel_posix, "bytes": size, "sha256": sha}
                for _, rel_posix, size, sha in pdf_entries
            ],
            "skipped_symlinks": skipped_symlinks,
            "notes": (
                "SRA local research library backup. Contains SQLite snapshot and optional PDFs only; "
                "never contains API keys, DPAPI/Keychain secrets, or network credentials."
            ),
        }

        staged_zip = staging_dir / "archive.zip"
        with zipfile.ZipFile(staged_zip, mode="w", allowZip64=True) as zf:
            zf.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                compress_type=zipfile.ZIP_DEFLATED,
            )
            zf.write(snapshot_db, arcname=DATABASE_ARCHIVE_NAME, compress_type=zipfile.ZIP_DEFLATED)
            for abs_path, rel_posix, _, _ in pdf_entries:
                # PDF 本身已压缩，使用 ZIP_STORED 显著降低大库备份耗时与 CPU 占用
                zf.write(abs_path, arcname=rel_posix, compress_type=zipfile.ZIP_STORED)

        shutil.move(str(staged_zip), str(target_zip))
        manifest["archive_path"] = str(target_zip)
        manifest["archive_bytes"] = target_zip.stat().st_size
        return manifest
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def verify_backup_archive(zip_path: Path) -> dict[str, Any]:
    """纯只读校验 `.zip` 备份包的路径安全、清单哈希与 SQLite 完整性。"""
    archive = Path(zip_path).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {
        "valid": False,
        "format_version": 0,
        "created_at": "",
        "include_pdfs": False,
        "contains_secrets": False,
        "db_bytes": 0,
        "pdf_count": 0,
        "pdf_bytes": 0,
        "counts": {},
        "warnings": warnings,
        "errors": errors,
    }
    if not archive.is_file():
        errors.append(f"备份文件不存在：{archive}")
        return result

    temp_dir = Path(tempfile.mkdtemp(prefix="sra-backup-verify-"))
    try:
        try:
            zf = zipfile.ZipFile(archive, mode="r")
        except zipfile.BadZipFile:
            errors.append("文件不是有效的 ZIP 压缩包。")
            return result

        with zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ZIP_ENTRIES:
                errors.append(f"归档条目过多（>{_MAX_ZIP_ENTRIES}），拒绝解压。")
                return result

            seen_names: set[str] = set()
            for info in infos:
                try:
                    posix = _safe_zip_member(info.filename)
                except ValueError as exc:
                    errors.append(str(exc))
                    return result
                lowered = posix.as_posix().lower()
                if any(secret_marker in lowered for secret_marker in (".dpapi", ".env", "model-keys")):
                    warnings.append(f"检测到非预期敏感文件名：{info.filename}")
                seen_names.add(posix.as_posix())

            if MANIFEST_NAME not in seen_names:
                errors.append(f"缺少备份清单 {MANIFEST_NAME}。")
            if DATABASE_ARCHIVE_NAME not in seen_names:
                errors.append(f"缺少核心数据库文件 {DATABASE_ARCHIVE_NAME}。")
            if errors:
                return result

            try:
                manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("manifest root must be a JSON object")
            except Exception as exc:
                errors.append(f"无法解析 {MANIFEST_NAME}：{exc}")
                return result

            fmt_ver = int(manifest.get("format_version", 0) or 0)
            if fmt_ver != BACKUP_FORMAT_VERSION:
                errors.append(f"不支持的备份格式版本：{fmt_ver}（期望 {BACKUP_FORMAT_VERSION}）。")
                return result

            result["format_version"] = fmt_ver
            result["created_at"] = str(manifest.get("created_at", ""))
            result["include_pdfs"] = bool(manifest.get("include_pdfs", False))
            result["contains_secrets"] = bool(manifest.get("contains_secrets", False))

            # 解压数据库到临时目录并校验 SHA-256 与 SQLite integrity_check
            extracted_db = temp_dir / DATABASE_ARCHIVE_NAME
            with zf.open(DATABASE_ARCHIVE_NAME, "r") as src, extracted_db.open("wb") as dst:
                shutil.copyfileobj(src, dst, _CHUNK_BYTES)

            actual_db_sha = _sha256_file(extracted_db)
            actual_db_bytes = extracted_db.stat().st_size
            result["db_bytes"] = actual_db_bytes

            db_meta = manifest.get("database") if isinstance(manifest.get("database"), dict) else {}
            expected_db_sha = str(db_meta.get("sha256", "") or "")
            if not expected_db_sha or actual_db_sha != expected_db_sha:
                errors.append("数据库 SHA-256 校验失败，备份包可能已损坏或被篡改。")
                return result

            integrity_errors = _sqlite_integrity_errors(extracted_db)
            if integrity_errors:
                errors.append("SQLite 完整性检查未通过：" + "; ".join(integrity_errors[:3]))
                return result

            actual_counts = _table_counts(extracted_db)
            result["counts"] = actual_counts
            expected_counts = manifest.get("counts")
            if isinstance(expected_counts, dict):
                for key, exp_val in expected_counts.items():
                    if key in actual_counts and int(actual_counts[key]) != int(exp_val):
                        errors.append(f"表 {key} 记录数与清单不符（实际 {actual_counts[key]}，清单 {exp_val}）。")

            # 如含 PDF 原文，流式逐个校验 SHA-256 与大小（不落盘大量临时 PDF）
            pdf_manifest = manifest.get("pdf_files")
            verified_pdfs = 0
            verified_pdf_bytes = 0
            if isinstance(pdf_manifest, list):
                for entry in pdf_manifest:
                    if not isinstance(entry, dict):
                        continue
                    rel_path = str(entry.get("path", "") or "")
                    exp_sha = str(entry.get("sha256", "") or "")
                    exp_bytes = int(entry.get("bytes", -1))
                    if rel_path not in seen_names:
                        errors.append(f"备份包缺少清单声明的 PDF：{rel_path}")
                        continue
                    digest = hashlib.sha256()
                    size = 0
                    with zf.open(rel_path, "r") as stream:
                        while chunk := stream.read(_CHUNK_BYTES):
                            digest.update(chunk)
                            size += len(chunk)
                    if size != exp_bytes or (exp_sha and digest.hexdigest() != exp_sha):
                        errors.append(f"PDF 文件校验失败：{rel_path}")
                        continue
                    verified_pdfs += 1
                    verified_pdf_bytes += size

            result["pdf_count"] = verified_pdfs
            result["pdf_bytes"] = verified_pdf_bytes
            if not result["include_pdfs"] and actual_counts.get("papers", 0) > 0:
                warnings.append(
                    "此备份为轻量模式（仅含数据库、笔记、审定与卡片，不含 PDF 原文）；"
                    "恢复后请确保原 PDF 目录仍保留或使用完整归档备份。"
                )
            result["valid"] = len(errors) == 0
            return result
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def restore_backup_archive(
    zip_path: Path,
    target_data_dir: Path,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """离线原子恢复研究库备份包，并自动重写 `papers.stored_path` 以支持跨机/跨盘符迁移。"""
    archive = Path(zip_path).expanduser().resolve()
    target = Path(target_data_dir).expanduser().resolve()

    verification = verify_backup_archive(archive)
    if not verification["valid"]:
        raise ValueError(
            "Backup verification failed; refusing to restore: "
            + "; ".join(verification["errors"])
        )

    existing_nonempty = target.exists() and any(target.iterdir())
    if existing_nonempty and not replace:
        raise FileExistsError(
            f"Target data directory is not empty: {target}. "
            "Stop the SRA desktop/API app first, then pass --replace to archive the old directory as .bak before restoring."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".sra-restore-stage-", dir=target.parent))
    backup_of_previous: Path | None = None
    try:
        with zipfile.ZipFile(archive, mode="r") as zf:
            # 1. 解压数据库
            db_target = staging / DATABASE_ARCHIVE_NAME
            with zf.open(DATABASE_ARCHIVE_NAME, "r") as src, db_target.open("wb") as dst:
                shutil.copyfileobj(src, dst, _CHUNK_BYTES)

            # 2. 如含 PDF 原文，安全解压至 staging/papers/
            restored_pdfs: dict[str, Path] = {}
            for info in zf.infolist():
                posix = _safe_zip_member(info.filename)
                if len(posix.parts) >= 2 and posix.parts[0] == "papers" and not info.is_dir():
                    rel_under_papers = Path(*posix.parts[1:])
                    dest_file = (staging / "papers" / rel_under_papers).resolve()
                    if not dest_file.is_relative_to((staging / "papers").resolve()):
                        raise ValueError(f"Unsafe PDF extraction path: {info.filename}")
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, "r") as src, dest_file.open("wb") as dst:
                        shutil.copyfileobj(src, dst, _CHUNK_BYTES)
                    restored_pdfs[rel_under_papers.as_posix()] = target / "papers" / rel_under_papers

        # 3. 事务性重写 papers.stored_path，使跨设备/跨盘符恢复后 PDF 路径自动生效
        rewritten_paths = 0
        if restored_pdfs:
            with closing(sqlite3.connect(db_target)) as conn:
                rows = conn.execute("SELECT id, stored_path FROM papers").fetchall()
                with conn:
                    for paper_id, old_stored in rows:
                        old_norm = str(old_stored or "").replace("\\", "/")
                        matched_new: Path | None = None
                        if "/papers/" in old_norm:
                            suffix = old_norm.rsplit("/papers/", 1)[1].lstrip("/")
                            matched_new = restored_pdfs.get(suffix)
                        if matched_new is None:
                            basename = old_norm.rsplit("/", 1)[-1]
                            matched_new = restored_pdfs.get(basename)
                        if matched_new is not None:
                            conn.execute(
                                "UPDATE papers SET stored_path = ? WHERE id = ?",
                                (str(matched_new), str(paper_id)),
                            )
                            rewritten_paths += 1

        # 4. 原子切换目标目录（保留旧库 .bak 副本，绝不盲删）
        if target.exists():
            if existing_nonempty:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                backup_of_previous = target.parent / f"{target.name}.bak-{stamp}"
                suffix_idx = 1
                while backup_of_previous.exists():
                    backup_of_previous = target.parent / f"{target.name}.bak-{stamp}-{suffix_idx}"
                    suffix_idx += 1
                target.rename(backup_of_previous)
            else:
                target.rmdir()

        staging.rename(target)
        return {
            "restored": True,
            "target_data_dir": str(target),
            "previous_backup_dir": str(backup_of_previous) if backup_of_previous else None,
            "include_pdfs": verification["include_pdfs"],
            "pdf_count": verification["pdf_count"],
            "rewritten_pdf_paths": rewritten_paths,
            "counts": verification["counts"],
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
