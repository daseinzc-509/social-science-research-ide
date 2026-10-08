"""Thin command-line entry point for the local prototype."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .analyzer import PROMPT_VERSION, PaperAnalysisError, PaperAnalysisPipeline
from .llm_client import ModelRequestError
from .library import LibraryService
from .references import extract_references
from .pipeline import ImportPipeline
from .repository import ResearchRepository


def _paths() -> tuple[Path, ResearchRepository]:
    data_dir = Path(os.environ.get("SRA_DATA_DIR", Path.cwd() / "data")).expanduser().resolve()
    repository = ResearchRepository(data_dir / "research.sqlite3")
    return data_dir, repository


def _progress(message: str) -> None:
    print(message, flush=True)


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TiB"


def _print_doctor_report(report: dict[str, object]) -> None:
    counts = report["counts"]
    storage = report["storage"]
    cache = report["cache"]
    issues = report["issues"]

    print(f"Status: {str(report['status']).upper()}")
    print(f"Database: {report['database_path']} ({_format_bytes(int(report['database_bytes']))})")
    print(f"PDF directory: {report['papers_dir']} ({_format_bytes(int(storage['papers_dir_pdf_bytes']))})")
    print(
        "Records: "
        f"{counts['papers']} papers, {counts['source_blocks']} source blocks, "
        f"{counts['paper_cards']} cards, {counts['user_notes']} notes, {counts['model_runs']} model cache runs"
    )
    print(
        "Stored payloads: "
        f"source text {_format_bytes(int(storage['source_text_bytes']))}, "
        f"cards {_format_bytes(int(storage['paper_card_json_bytes']))}, "
        f"model cache {_format_bytes(int(storage['model_cache_json_bytes']))}"
    )

    current_version = cache["current_prompt_version"]
    versions = cache["prompt_versions"]
    version_text = ", ".join(f"{name}={count}" for name, count in versions.items()) or "none"
    print(f"Prompt cache versions: {version_text}")
    if current_version:
        print(
            f"Stale cache vs {current_version}: {cache['stale_runs']} runs "
            f"({_format_bytes(int(cache['stale_bytes']))})"
        )

    if report["papers_without_cards"]:
        print(f"Info: {len(report['papers_without_cards'])} imported paper(s) do not have a Paper Card yet.")

    issue_lines: list[str] = []
    if issues["integrity_errors"]:
        issue_lines.append(f"SQLite integrity errors: {len(issues['integrity_errors'])}")
    if issues["foreign_key_errors"]:
        issue_lines.append(f"Foreign-key errors: {len(issues['foreign_key_errors'])}")
    if issues["missing_pdfs"]:
        issue_lines.append(f"Missing referenced PDFs: {len(issues['missing_pdfs'])}")
    if issues["hash_mismatches"]:
        issue_lines.append(f"PDF hash mismatches: {len(issues['hash_mismatches'])}")
    if issues["invalid_cards"]:
        issue_lines.append(f"Invalid Paper Cards: {len(issues['invalid_cards'])}")
    if issues["papers_without_source_blocks"]:
        issue_lines.append(f"Papers without source blocks: {len(issues['papers_without_source_blocks'])}")
    if issues["duplicate_stored_paths"]:
        issue_lines.append(f"Duplicate stored paths: {len(issues['duplicate_stored_paths'])}")
    if issues["untracked_pdfs"]:
        issue_lines.append(
            f"Untracked PDFs: {len(issues['untracked_pdfs'])} "
            f"({_format_bytes(int(storage['untracked_pdf_bytes']))})"
        )

    if not issue_lines:
        print("Repository checks: OK")
    else:
        print("Repository checks need review:")
        for line in issue_lines:
            print(f"  - {line}")

    if not report["deep"]:
        print("Tip: run `sra doctor --deep` to verify every stored PDF against its SHA-256.")
    if int(cache["stale_runs"]) > 0:
        print("Tip: run `sra prune-cache` to preview removal of stale model cache rows.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sra", description="Evidence-first social science paper workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    import_cmd = commands.add_parser("import-pdf", help="Import and parse a local PDF")
    import_cmd.add_argument("pdf", type=Path)
    batch_cmd = commands.add_parser("import-pdf-dir", help="Import every PDF in a directory")
    batch_cmd.add_argument("path", type=Path)
    batch_cmd.add_argument("--no-recursive", action="store_true", help="Only scan the immediate directory")
    batch_cmd.add_argument("--json", action="store_true", help="Print per-file results as JSON")
    commands.add_parser("list", help="List imported papers")
    show_cmd = commands.add_parser("show", help="Show a paper and its parse status")
    show_cmd.add_argument("paper_id")
    export_cmd = commands.add_parser("export-card", help="Export a saved Paper Card as JSON")
    export_cmd.add_argument("paper_id")
    export_cmd.add_argument("--output", type=Path, required=True)
    text_cmd = commands.add_parser("export-text", help="Export parsed source blocks with page and block IDs")
    text_cmd.add_argument("paper_id")
    text_cmd.add_argument("--output", type=Path, required=True)
    refs_cmd = commands.add_parser("extract-references", help="Extract references and citation mentions from a parsed paper")
    refs_cmd.add_argument("paper_id")
    refs_cmd.add_argument("--output", type=Path, required=True)
    refs_cmd.add_argument("--format", choices=("json", "bibtex"), default="json")
    export_refs_cmd = commands.add_parser("export-references", help="Export saved references for Zotero or other reference managers")
    export_refs_cmd.add_argument("paper_id")
    export_refs_cmd.add_argument("--output", type=Path, required=True)
    export_refs_cmd.add_argument("--format", choices=("json", "bibtex"), default="bibtex")
    analyze_cmd = commands.add_parser("analyze", help="Run Lite extraction followed by Pro analysis")
    analyze_cmd.add_argument("paper_id")
    analyze_cmd.add_argument("--research-context", help="Optional project question; used only for relevance assessment")
    analyze_cmd.add_argument("--exclude-after-text", help="Exclude this marker and all following blocks (for adjacent articles)")
    analyze_cmd.add_argument("--run", action="store_true", help="Send model requests and consume Agent Plan quota; default is a no-call preview")
    analyze_cmd.add_argument("--force", action="store_true", help="Ignore cached model results and call both stages again")
    doctor_cmd = commands.add_parser("doctor", help="Inspect repository health, storage, duplicates, and stale model cache")
    doctor_cmd.add_argument("--deep", action="store_true", help="Also hash every stored PDF and compare it with the recorded SHA-256")
    doctor_cmd.add_argument("--json", action="store_true", help="Print the complete diagnostic report as JSON")
    prune_cmd = commands.add_parser("prune-cache", help="Preview or remove model-response cache rows only")
    prune_cmd.add_argument("--all", action="store_true", help="Target all model cache rows instead of only old prompt versions")
    prune_cmd.add_argument("--yes", action="store_true", help="Actually delete the selected cache rows; without this flag the command is a dry run")
    prune_cmd.add_argument("--vacuum", action="store_true", help="Compact the SQLite file after deletion; requires --yes")
    ui_cmd = commands.add_parser("ui", help="Open the local browser workspace")
    ui_cmd.add_argument("--port", type=int, default=8765, help="Preferred local port; falls forward if busy (default: 8765)")
    ui_cmd.add_argument("--no-browser", action="store_true", help="Start the UI without opening a browser tab")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir, repository = _paths()
    try:
        if args.command == "import-pdf":
            record, parsed, created = ImportPipeline(data_dir, repository).import_pdf(args.pdf)
            message = "Imported" if created else "Already imported"
            print(f"{message}: {record.id} ({record.page_count} pages, {record.parse_status.value})")
            for warning in parsed.warnings:
                print(f"Warning: {warning}")
            return 0
        if args.command == "import-pdf-dir":
            service = LibraryService(data_dir, repository, progress=_progress)
            paths = service.scan_pdfs(args.path, recursive=not args.no_recursive)
            if not paths:
                if not args.path.exists():
                    print(f"No such path: {args.path}", file=sys.stderr)
                    return 2
                print("No PDF files found.")
                return 0
            results = service.batch_import(paths)
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                counts: dict[str, int] = {}
                for result in results:
                    status = str(result["status"])
                    counts[status] = counts.get(status, 0) + 1
                    suffix = f" — {result['message']}" if result.get("message") else ""
                    paper_id = f" [{result['paper_id']}]" if result.get("paper_id") else ""
                    print(f"{status.upper():9} {result['path']}{paper_id}{suffix}")
                print("Summary: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
            return 0 if not any(result["status"] == "failed" for result in results) else 1
        if args.command == "list":
            for paper in repository.list_papers():
                print(f"{paper.id}\t{paper.parse_status.value}\t{paper.original_name}")
            return 0
        if args.command == "show":
            paper = repository.get_paper(args.paper_id)
            if paper is None:
                print(f"Unknown paper id: {args.paper_id}", file=sys.stderr)
                return 2
            print(paper.model_dump_json(indent=2))
            card = repository.get_card(paper.id)
            if card is not None:
                print("Paper Card:")
                print(card.model_dump_json(indent=2))
            return 0
        if args.command == "export-card":
            card = repository.get_card(args.paper_id)
            if card is None:
                print("No Paper Card has been saved for this paper yet.", file=sys.stderr)
                return 2
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(card.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Exported Paper Card to {args.output}")
            return 0
        if args.command == "export-text":
            if repository.get_paper(args.paper_id) is None:
                print(f"Unknown paper id: {args.paper_id}", file=sys.stderr)
                return 2
            blocks = repository.get_source_blocks(args.paper_id)
            if not blocks:
                print("No parsed source blocks are available.", file=sys.stderr)
                return 2
            args.output.parent.mkdir(parents=True, exist_ok=True)
            content = "\n\n".join(
                f"[PDF page {block.page_number} | source_block_id={block.id}]\n{block.text}"
                for block in blocks
            )
            args.output.write_text(content, encoding="utf-8")
            print(f"Exported {len(blocks)} source blocks to {args.output}")
            return 0
        if args.command == "extract-references":
            if repository.get_paper(args.paper_id) is None:
                print(f"Unknown paper id: {args.paper_id}", file=sys.stderr)
                return 2
            blocks = repository.get_source_blocks(args.paper_id)
            entries, mentions = extract_references(args.paper_id, blocks)
            repository.save_references(args.paper_id, entries, mentions)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "paper_id": args.paper_id,
                "references": [entry.model_dump(mode="json") for entry in entries],
                "citation_mentions": [mention.model_dump(mode="json") for mention in mentions],
            }
            content = json.dumps(payload, ensure_ascii=False, indent=2) if args.format == "json" else _references_to_bibtex(entries)
            args.output.write_text(content, encoding="utf-8")
            print(f"Extracted {len(entries)} references and {len(mentions)} citation mentions to {args.output}")
            return 0
        if args.command == "export-references":
            if repository.get_paper(args.paper_id) is None:
                print(f"Unknown paper id: {args.paper_id}", file=sys.stderr)
                return 2
            entries, mentions = repository.get_references(args.paper_id)
            if not entries:
                print("No saved references found. Run extract-references first.", file=sys.stderr)
                return 2
            args.output.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "paper_id": args.paper_id,
                "references": [entry.model_dump(mode="json") for entry in entries],
                "citation_mentions": [mention.model_dump(mode="json") for mention in mentions],
            }
            content = json.dumps(payload, ensure_ascii=False, indent=2) if args.format == "json" else _references_to_bibtex(entries)
            args.output.write_text(content, encoding="utf-8")
            print(f"Exported {len(entries)} references to {args.output}")
            return 0
        if args.command == "doctor":
            report = repository.diagnose(
                data_dir / "papers", current_prompt_version=PROMPT_VERSION, deep=args.deep
            )
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                _print_doctor_report(report)
            return 0 if report["status"] == "ok" else 1
        if args.command == "prune-cache":
            result = repository.prune_model_runs(
                current_prompt_version=PROMPT_VERSION,
                all_runs=args.all,
                apply=args.yes,
                vacuum=args.vacuum,
            )
            scope = "all model cache" if args.all else f"stale cache (prompt version != {PROMPT_VERSION})"
            if not args.yes:
                print(
                    f"Dry run: {result['matched_runs']} run(s), {_format_bytes(int(result['matched_bytes']))} "
                    f"would be removed from {scope}."
                )
                if int(result["matched_runs"]) > 0:
                    print("Re-run with --yes to delete them. Paper Cards, PDFs, source blocks, and notes are never touched.")
                else:
                    print("Nothing to prune.")
                return 0
            print(
                f"Deleted {result['deleted_runs']} model cache run(s) from {scope}; "
                f"{result['total_runs_after']} run(s) remain."
            )
            if args.vacuum:
                before = _format_bytes(int(result["database_bytes_before"]))
                after = _format_bytes(int(result["database_bytes_after"]))
                print(f"VACUUM completed: database {before} -> {after}.")
            return 0
        if args.command == "ui":
            from .ui import serve_ui

            if not 0 <= args.port <= 65535:
                raise ValueError("--port must be between 0 and 65535")
            serve_ui(data_dir, repository, port=args.port, open_browser=not args.no_browser)
            return 0
        if args.command == "analyze":
            pipeline = PaperAnalysisPipeline(repository, progress=_progress)
            if not args.run:
                if args.force:
                    print("Error: --force requires --run.", file=sys.stderr)
                    return 2
                plan = pipeline.preview(args.paper_id, exclude_after_text=args.exclude_after_text)
                print("Preview only: no model request was sent and no quota was consumed.", flush=True)
                print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
                print("Add --run to send requests. Research context is optional and only affects relevance assessment.", flush=True)
                return 0
            print("Sending Lite extraction then independent Pro source review. Successful stage results are cached; no automatic retries are made.", flush=True)
            card = pipeline.analyze(
                args.paper_id,
                research_context=args.research_context,
                exclude_after_text=args.exclude_after_text,
                force=args.force,
            )
            print(
                f"Paper Card saved: {card.paper_id} "
                f"({len(card.basic_facts)} facts, {len(card.claim_audits)} semantic audits, "
                f"study={card.study_profile.study_type.value if card.study_profile else 'unknown'}, "
                f"{len(card.evidence_spans)} evidence spans, {len(card.tables)} parsed tables, "
                f"{len(card.analysis)} analysis claims, {len(card.warnings)} warnings)"
            )
            for warning in card.warnings:
                print(f"Warning: {warning}", file=sys.stderr)
            return 0
    except (OSError, ValueError, KeyError, PaperAnalysisError, ModelRequestError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 2


def _references_to_bibtex(entries: list) -> str:
    blocks: list[str] = []
    for index, entry in enumerate(entries, start=1):
        key = _bibtex_key(entry, index)
        fields = [("author", " and ".join(entry.authors)), ("title", entry.title), ("year", entry.year),
                  ("journal", entry.container_title), ("volume", entry.volume), ("number", entry.issue),
                  ("pages", entry.pages), ("doi", entry.doi), ("url", entry.url)]
        lines = [f"@article{{{key},"]
        for name, value in fields:
            if value not in (None, "", []):
                escaped = str(value).replace("{", "\\{").replace("}", "\\}")
                lines.append(f"  {name} = {{{escaped}}},")
        lines.append(f"  note = {{Imported from PDF p. {entry.source_page}; review raw_text before citing}},")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _bibtex_key(entry, index: int) -> str:
    author = (entry.authors[0] if entry.authors else "ref").split()[-1]
    author = "".join(character for character in author if character.isalnum()) or "ref"
    return f"{author}{entry.year or 'nd'}_{index}"


if __name__ == "__main__":
    raise SystemExit(main())
