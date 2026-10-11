'''Guard against the original v3-v5 integration regression.

These checks intentionally inspect the ACTUAL installed repo, rather than
passing merely because standalone v5 code or isolated API tests exist.
'''
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def installer():
    path = ROOT / "scripts/repair_region_review_ci.py"
    spec = importlib.util.spec_from_file_location("sra_region_review_ci_repair", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v3_v4_v5_region_review_client_methods_really_installed():
    module = installer()
    client = read(module.CLIENT_FILE)
    expected = [name for _,_,names in module.GROUPS for name in names]
    assert len(expected) == 12
    for method in expected:
        assert len(re.findall(r"\b" + re.escape(method) + r"\s*\(", client)) == 1, (
            f"Missing or duplicated method: SraApiClient.{method}"
        )
    assert '_http.DefaultRequestHeaders.Add("X-SRA-Desktop-Token"' in client
    assert 'new HttpClient' in client


def test_region_review_router_is_mounted_inside_token_protected_desktop_app():
    from_source = read("src/sociology_research/api/app.py")
    middleware = read("src/sociology_research/api/desktop_entry.py")
    route = read("src/sociology_research/api/routes/region_review.py")
    assert from_source.count("app.include_router(region_review.router, prefix=API_PREFIX)") == 1
    assert "from .routes import region_review" in from_source or re.search(
        r"^from \.routes import .*\bregion_review\b", from_source, re.M)
    assert 'prefix="/papers/{paper_id}/region-review"' in route
    assert "class DesktopTokenMiddleware" in middleware
    assert "app.add_middleware(DesktopTokenMiddleware" in middleware


def test_avalonia_12_clipboard_extensions_resolved_in_both_windows():
    project = read("desktop/SRA.Desktop/SRA.Desktop.csproj")
    assert 'PackageReference Include="Avalonia" Version="12.' in project
    for file in ("desktop/SRA.Desktop/Views/RegionReviewWindow.cs",
                 "desktop/SRA.Desktop/Views/ReviewedComparisonWindow.cs"):
        window=read(file)
        assert "using Avalonia.Input.Platform;" in window, file
        assert "clipboard.SetTextAsync(" in window, file
    assert "Watermark =" not in read("desktop/SRA.Desktop/Views/RegionReviewWindow.cs")


def test_ci_fixer_safe_and_idempotent_without_any_user_data():
    module=installer()
    old_client = '''using System.Net.Http.Json;
namespace SRA.Desktop.Services;
public sealed class SraApiClient : IDisposable
{
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _jsonOptions;
    private Task<T> GetJsonAsync<T>() => throw new NotImplementedException();
    private Task<T> ReadJsonAsync<T>() => throw new NotImplementedException();
    private Task EnsureSuccessAsync() => throw new NotImplementedException();
    private async Task<string> GetTextAsync(string url, CancellationToken ct) =>
        await Task.FromResult(url);
}
'''
    updated=module.update_client(old_client)
    assert updated.count("private async Task<string> GetTextAsync(")==1
    for _,_,names in module.GROUPS:
        for method in names:
            assert updated.count(method+"(")==1
    assert module.update_client(updated)==updated
    assert "private readonly HttpClient _http;" in updated
    assert "namespace SRA.Desktop.Services;" in updated


def test_no_remaining_patches_required_after_full_install():
    assert installer().stage(ROOT.resolve()) == {}, (
        "Source integration incomplete. Run: "
        "py -3 scripts/repair_region_review_ci.py --repo . --dry-run"
    )
