#!/usr/bin/env python3
"""Repair SRA v3-v5 region-review integration, without overwriting application code.

Only fills missing groups of SraApiClient methods, adds the missing authenticated
region-review router registration and imports the Avalonia 12 clipboard extension
namespace. It never edits data, PDFs, Paper Cards or model settings.

Commands (from the repository root):
    py -3 scripts/repair_region_review_ci.py --repo . --dry-run
    py -3 scripts/repair_region_review_ci.py --repo .
    py -3 scripts/repair_region_review_ci.py --repo . --check
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


class RepairError(RuntimeError):
    pass


V3_METHODS = '\n    /// <summary>Render a paper PDF page without exposing the desktop token in the URL.</summary>\n    public async Task<(byte[] Png, int Pages, double PageWidth, double PageHeight)> GetRegionReviewPageAsync(\n        string paperId, int pageNumber, CancellationToken cancellationToken = default)\n    {\n        using var response = await _http.GetAsync(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/pages/{pageNumber}.png",\n            cancellationToken);\n        await EnsureSuccessAsync(response, cancellationToken);\n        static string RequiredHeader(HttpResponseMessage reply, string name)\n        {\n            if (!reply.Headers.TryGetValues(name, out var values))\n                throw new InvalidOperationException($"PDF review backend omitted {name}");\n            return values.FirstOrDefault() ?? throw new InvalidOperationException(name + " is empty");\n        }\n        var pageCount = int.Parse(RequiredHeader(response, "X-SRA-Page-Count"),\n            System.Globalization.CultureInfo.InvariantCulture);\n        var width = double.Parse(RequiredHeader(response, "X-SRA-Page-Width"),\n            System.Globalization.CultureInfo.InvariantCulture);\n        var height = double.Parse(RequiredHeader(response, "X-SRA-Page-Height"),\n            System.Globalization.CultureInfo.InvariantCulture);\n        return (await response.Content.ReadAsByteArrayAsync(cancellationToken), pageCount, width, height);\n    }\n\n    public Task<JsonElement> GetRegionReviewBlocksAsync(\n        string paperId, int pageNumber, CancellationToken cancellationToken = default) =>\n        GetJsonAsync<JsonElement>(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/pages/{pageNumber}/blocks",\n            cancellationToken);\n\n    public async Task<JsonElement> PreviewPdfRegionAsync(\n        string paperId, int pageNumber, double x0, double y0, double x1, double y1,\n        bool manualOnly = false, CancellationToken cancellationToken = default)\n    {\n        using var response = await _http.PostAsJsonAsync(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/pages/{pageNumber}/preview",\n            new { bounds = new { x0, y0, x1, y1 }, manual_only = manualOnly },\n            _jsonOptions, cancellationToken);\n        return await ReadJsonAsync<JsonElement>(response, cancellationToken);\n    }\n\n    public async Task<JsonElement> ConfirmPdfRegionAsync(\n        string paperId, string previewId, string correctedText, string note,\n        CancellationToken cancellationToken = default)\n    {\n        using var response = await _http.PostAsJsonAsync(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/confirm",\n            new { preview_id = previewId, corrected_text = correctedText, researcher_note = note },\n            _jsonOptions, cancellationToken);\n        return await ReadJsonAsync<JsonElement>(response, cancellationToken);\n    }\n\n    public Task<JsonElement> GetPdfRegionReviewsAsync(\n        string paperId, CancellationToken cancellationToken = default) =>\n        GetJsonAsync<JsonElement>(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/reviews",\n            cancellationToken);\n\n    public async Task<JsonElement> RevokePdfRegionReviewAsync(\n        string paperId, string reviewId, CancellationToken cancellationToken = default)\n    {\n        using var response = await _http.PostAsync(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/reviews/{Uri.EscapeDataString(reviewId)}/revoke",\n            content: null, cancellationToken);\n        return await ReadJsonAsync<JsonElement>(response, cancellationToken);\n    }\n\n'

V3_NAMES = ('GetRegionReviewPageAsync', 'GetRegionReviewBlocksAsync', 'PreviewPdfRegionAsync', 'ConfirmPdfRegionAsync', 'GetPdfRegionReviewsAsync', 'RevokePdfRegionReviewAsync')

V4_METHODS = '\n    /// <summary>\n    /// Create a new confirmed human revision; never change Docling evidence.\n    /// </summary>\n    public async Task<JsonElement> RevisePdfRegionReviewAsync(\n        string paperId, string reviewId, int expectedRevision, string correctedText,\n        string researcherNote, CancellationToken cancellationToken = default)\n    {\n        using var response = await _http.PostAsJsonAsync(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/reviews/{Uri.EscapeDataString(reviewId)}/revise",\n            new {\n                expected_revision = expectedRevision,\n                corrected_text = correctedText,\n                researcher_note = researcherNote,\n            },\n            _jsonOptions, cancellationToken);\n        return await ReadJsonAsync<JsonElement>(response, cancellationToken);\n    }\n\n    /// <summary>Retrieve a user-requested, local, read-only correction supplement.</summary>\n    public Task<JsonElement> GetPdfRegionReviewPacketAsync(\n        string paperId, CancellationToken cancellationToken = default) =>\n        GetJsonAsync<JsonElement>(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/packet",\n            cancellationToken);\n\n'

V4_NAMES = ('RevisePdfRegionReviewAsync', 'GetPdfRegionReviewPacketAsync')

V5_METHODS = '\n    /// <summary>Local preview only. Does NOT contact a Pro provider.</summary>\n    public Task<JsonElement> PreviewReviewedComparisonAsync(\n        string paperId, CancellationToken cancellationToken = default) =>\n        GetJsonAsync<JsonElement>(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/reanalysis/preview",\n            cancellationToken);\n\n    /// <summary>Runs exactly one user-approved, potentially billable Pro comparison.</summary>\n    public async Task<JsonElement> RunReviewedComparisonAsync(\n        string paperId, string snapshotDigest, bool externalConsent, bool costConsent,\n        CancellationToken cancellationToken = default)\n    {\n        using var response = await _http.PostAsJsonAsync(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/reanalysis/run",\n            new {\n                expected_snapshot_digest = snapshotDigest,\n                approve_external_send = externalConsent,\n                acknowledge_api_cost = costConsent,\n            },\n            _jsonOptions, cancellationToken);\n        return await ReadJsonAsync<JsonElement>(response, cancellationToken);\n    }\n\n    /// <summary>List immutable historical reviewed-comparison cards.</summary>\n    public Task<JsonElement> ListReviewedComparisonsAsync(\n        string paperId, CancellationToken cancellationToken = default) =>\n        GetJsonAsync<JsonElement>(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/reanalysis/reports",\n            cancellationToken);\n\n    /// <summary>Read a historical comparison without calling a model.</summary>\n    public Task<JsonElement> GetReviewedComparisonAsync(\n        string paperId, string analysisId,\n        CancellationToken cancellationToken = default) =>\n        GetJsonAsync<JsonElement>(\n            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/region-review/reanalysis/reports/{Uri.EscapeDataString(analysisId)}",\n            cancellationToken);\n\n'

V5_NAMES = ('PreviewReviewedComparisonAsync', 'RunReviewedComparisonAsync', 'ListReviewedComparisonsAsync', 'GetReviewedComparisonAsync')


CLIENT_FILE = "desktop/SRA.Desktop/Services/SraApiClient.cs"
APP_FILE = "src/sociology_research/api/app.py"
ROUTE_FILE = "src/sociology_research/api/routes/region_review.py"
SECURE_ENTRY_FILE = "src/sociology_research/api/desktop_entry.py"
REGION_WINDOW = "desktop/SRA.Desktop/Views/RegionReviewWindow.cs"
COMPARISON_WINDOW = "desktop/SRA.Desktop/Views/ReviewedComparisonWindow.cs"
PROJECT_FILE = "desktop/SRA.Desktop/SRA.Desktop.csproj"

GROUPS = (
    ("v3", V3_METHODS, V3_NAMES),
    ("v4", V4_METHODS, V4_NAMES),
    ("v5", V5_METHODS, V5_NAMES),
)

CLIENT_ANCHOR = "    private async Task<string> GetTextAsync("
ROUTER_REGISTRATION = "    app.include_router(region_review.router, prefix=API_PREFIX)"


def _present(source: str, name: str) -> bool:
    return bool(re.search(r"\b" + re.escape(name) + r"\s*\(", source))


def update_client(source: str) -> str:
    # Verify the actual private helper contract of this SraApiClient. The
    # inserted snippets reuse its authenticated HttpClient; never create a
    # separate, unprotected HttpClient or leak session tokens in the URL.
    required = (
        "class SraApiClient",
        "_http",
        "_jsonOptions",
        "GetJsonAsync<",
        "ReadJsonAsync<",
        "EnsureSuccessAsync(",
    )
    for symbol in required:
        if symbol not in source:
            raise RepairError(f"API client lacks required existing helper: {symbol}")

    additions = []
    for label, code, names in GROUPS:
        found = [name for name in names if _present(source, name)]
        if found and len(found) != len(names):
            raise RepairError(
                f"Only some {label} API methods are present: {', '.join(found)}. "
                "Please compare your local edits; the fix refuses to insert duplicates."
            )
        if not found:
            additions.append(f"    // SRA Region Review {label}: authenticated local API bridge\n"
                             + code.rstrip() + "\n\n")

    if not additions:
        return source
    if source.count(CLIENT_ANCHOR) != 1:
        raise RepairError("Cannot find unique GetTextAsync helper in SraApiClient.cs")
    return source.replace(CLIENT_ANCHOR, "".join(additions) + CLIENT_ANCHOR, 1)


def update_app(source: str) -> str:
    # The router is mounted by the token-protected desktop entrypoint.
    imported = bool(re.search(r"^from \.routes import .*\bregion_review\b", source, re.M)) or (
        "from .routes import region_review" in source
    )
    if not imported:
        lines = source.splitlines(keepends=True)
        route_lines = [i for i, value in enumerate(lines) if value.startswith("from .routes import ")]
        if len(route_lines) != 1:
            raise RepairError("Cannot safely locate the existing .routes import in api/app.py")
        lines.insert(route_lines[0] + 1, "from .routes import region_review\n")
        source = "".join(lines)

    if ROUTER_REGISTRATION not in source:
        anchor = "    app.include_router(workspace.router, prefix=API_PREFIX)"
        if source.count(anchor) != 1:
            raise RepairError("Cannot safely locate the router registration anchor")
        source = source.replace(anchor, anchor + "\n" + ROUTER_REGISTRATION, 1)
    if source.count(ROUTER_REGISTRATION) != 1:
        raise RepairError("Duplicate or inconsistent region-review router registration")
    return source


def update_view(source: str, *, region_window: bool) -> str:
    if "clipboard.SetTextAsync(" in source and "using Avalonia.Input.Platform;" not in source:
        # Avalonia 12 keeps clipboard.SetTextAsync as a ClipboardExtensions
        # extension method in Avalonia.Input.Platform, not on IClipboard itself.
        marker = "using Avalonia.Controls;"
        if source.count(marker) != 1:
            raise RepairError("Cannot find a safe using directive for clipboard extension")
        source = source.replace(marker, marker + "\nusing Avalonia.Input.Platform;", 1)

    if region_window:
        # Avalonia 12 has replaced TextBox.Watermark with PlaceholderText.
        source = source.replace("Watermark =", "PlaceholderText =")
    return source


def _text_with_format(path: Path) -> tuple[str, str, bool]:
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    source = raw.decode("utf-8-sig" if bom else "utf-8")
    ending = "\r\n" if b"\r\n" in raw else "\n"
    return source.replace("\r\n", "\n"), ending, bom


def _encode(source: str, newline: str, bom: bool) -> bytes:
    return (b"\xef\xbb\xbf" if bom else b"") + source.replace("\n", newline).encode("utf-8")


def stage(repo: Path) -> dict[Path, bytes]:
    for rel in (CLIENT_FILE, APP_FILE, ROUTE_FILE, SECURE_ENTRY_FILE,
                REGION_WINDOW, COMPARISON_WINDOW, PROJECT_FILE):
        if not (repo / rel).is_file():
            raise RepairError(f"Required existing SRA source file is missing: {rel}")

    # Without this middleware, router registration could inadvertently make
    # human PDF corrections and model operations accessible without the token.
    entry = (repo / SECURE_ENTRY_FILE).read_text(encoding="utf-8")
    if ("DesktopTokenMiddleware" not in entry
            or "app.add_middleware(DesktopTokenMiddleware" not in entry):
        raise RepairError(
            "DesktopTokenMiddleware was not found in desktop_entry.py; "
            "refusing to register region-review endpoints without authentication"
        )
    route_source = (repo / ROUTE_FILE).read_text(encoding="utf-8")
    if 'prefix="/papers/{paper_id}/region-review"' not in route_source:
        raise RepairError("Unexpected region-review API route prefix; aborting")
    project = (repo / PROJECT_FILE).read_text(encoding="utf-8")
    if 'PackageReference Include="Avalonia"' not in project:
        raise RepairError("Cannot confirm an Avalonia desktop project in SRA.Desktop.csproj")

    changes: dict[Path, bytes] = {}
    fixes = {
        CLIENT_FILE: update_client,
        APP_FILE: update_app,
        REGION_WINDOW: lambda text: update_view(text, region_window=True),
        COMPARISON_WINDOW: lambda text: update_view(text, region_window=False),
    }
    for rel, patch in fixes.items():
        path = repo / rel
        original, newline, bom = _text_with_format(path)
        updated = patch(original)
        if updated != original:
            if path.suffix == ".py":
                compile(updated, str(path), "exec")
            changes[path] = _encode(updated, newline, bom)

    return changes


def apply(repo: Path, changes: dict[Path, bytes]) -> Path | None:
    if not changes:
        return None
    backup = Path(tempfile.mkdtemp(prefix="sra-v5-ci-repair-backup-"))
    # Always finish backing up ALL originals before writing any changes.
    for path in changes:
        original = backup / path.relative_to(repo)
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, original)
    written = []
    try:
        for target, content in changes.items():
            descriptor, staged = tempfile.mkstemp(prefix=".sra-ci-repair-", dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                os.replace(staged, target)
            finally:
                if os.path.exists(staged):
                    os.unlink(staged)
            written.append(target)
    except Exception:
        for target in reversed(written):
            shutil.copy2(backup / target.relative_to(repo), target)
        raise
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="Return exit code 2 if any integration fix is still needed")
    args = parser.parse_args()
    root = args.repo.expanduser().resolve()
    changes = stage(root)
    if not changes:
        print("SRA v3-v5 integration already complete. Nothing to change.")
        return 0

    print("Required source fixes:")
    for path in changes:
        print("  " + path.relative_to(root).as_posix())
    if args.check:
        print("Integration contract failed: source fixes have not been applied.")
        return 2
    if args.dry_run:
        print("Dry-run complete; no files changed.")
        return 0

    backup = apply(root, changes)
    print(f"Integration repair applied; backups: {backup}")
    print("Run pytest integration checks and dotnet build on Windows/macOS.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RepairError, OSError, SyntaxError, UnicodeError) as exc:
        print("SRA CI REPAIR ABORTED:", exc, file=sys.stderr)
        raise SystemExit(2)
