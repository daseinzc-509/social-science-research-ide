#!/usr/bin/env python3
"""Transactional source integrator for SRA's real API, Docling parser and Avalonia UI.

This script refuses unknown wiring rather than generating a 'successful' unconnected UI.
It never reads credential files, .env, databases or personal model caches.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class IntegrationMismatch(RuntimeError):
    pass

# Exact hashes of the immediately preceding candidate; no arbitrary overwrites.
KNOWN_OLD_CANDIDATE = {'src/sociology_research/model_center.py': '6bd0d02c90432adf82fa1e562b3c02cf78c872aaa997db9e21e02f4ea358b176', 'src/sociology_research/api/routes/model_center.py': 'e8c54ceeac3fd25e7b7d813b4ca0585fa43494ca1967a8a6c27c88e2692fa54f'}


def brace_close(text: str, opening: int) -> int:
    """Find a C# class brace, skipping ordinary strings, chars and comments."""
    if text[opening] != '{':
        raise IntegrationMismatch("内部错误：不是左花括号")
    depth = 0
    pos, mode = opening, 'code'
    while pos < len(text):
        c = text[pos]
        nxt = text[pos+1] if pos+1 < len(text) else ''
        if mode == 'line':
            if c == '\n': mode = 'code'
        elif mode == 'block':
            if c == '*' and nxt == '/': pos += 1; mode = 'code'
        elif mode == 'string':
            if c == '\\': pos += 1
            elif c == '"': mode = 'code'
        elif mode == 'verbatim':
            if c == '"' and nxt == '"': pos += 1
            elif c == '"': mode = 'code'
        elif mode == 'char':
            if c == '\\': pos += 1
            elif c == "'": mode = 'code'
        elif c == '/' and nxt == '/': mode = 'line'; pos += 1
        elif c == '/' and nxt == '*': mode = 'block'; pos += 1
        elif c == '@' and nxt == '"': mode = 'verbatim'; pos += 1
        elif c == '"': mode = 'string'
        elif c == "'": mode = 'char'
        elif c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: return pos
        pos += 1
    raise IntegrationMismatch('C# 源文件缺少对应的右花括号')


def insert_in_class(code: str, class_name: str, snippet: str) -> str:
    found = re.search(r'\bclass\s+' + re.escape(class_name) + r'\b[^\{]*\{', code)
    if found is None:
        raise IntegrationMismatch(f'找不到 C# class {class_name}')
    end = brace_close(code, found.end() - 1)
    return code[:end] + '\n' + snippet.rstrip() + '\n' + code[end:]


def route_patch(code: str) -> str:
    if 'def create_app(' not in code or 'app.include_router(' not in code:
        raise IntegrationMismatch('API app.py 无法识别 create_app()/include_router()')
    idx = code.find('API_PREFIX =')
    if idx == -1:
        raise IntegrationMismatch('API app.py 缺少 API_PREFIX')
    if 'from .routes import model_center' not in code:
        code = code[:idx] + 'from .routes import model_center\n' + code[idx:]
    if 'model_center_core' not in code:
        idx = code.find('API_PREFIX =')
        code = code[:idx] + 'from .. import model_center as model_center_core\n' + code[idx:]
    anchor = re.search(r'^([ \t]*)app\.include_router\(settings\.router,\s*prefix=API_PREFIX\)[ \t]*$', code, re.M)
    if 'app.include_router(model_center.router' not in code:
        if not anchor:
            raise IntegrationMismatch('API app.py 未找到 settings.router 注册行')
        indent = anchor.group(1)
        code = code[:anchor.end()] + f'\n{indent}app.include_router(model_center.router, prefix=API_PREFIX)' + code[anchor.end():]
    # Apply active credentials/artifacts before any services/parsers are initialized.
    start = re.search(r'\bdef create_app\([\s\S]*?\)\s*->\s*FastAPI\s*:', code)
    if not start:
        start = re.search(r'\bdef create_app\([\s\S]*?\)\s*:', code)
    if not start:
        raise IntegrationMismatch('无法安全找到 create_app 的函数体')
    after = code[start.end():]
    line = re.search(r'\n(    )(?:#.*\n\s*)?(if services is None:|resolved_data_dir\s*=|app = FastAPI\()', after)
    # Normal composition root can have comments and if-guards; prepare first.
    if 'model_center_core.prepare_runtime()' not in code:
        code = code[:start.end()] + '\n    model_center_core.prepare_runtime()' + code[start.end():]
    # When imports.router has no existing dependency, add preflight to prevent 7× failures.
    code, n = re.subn(r'app\.include_router\(imports\.router,\s*prefix=API_PREFIX\)',
                      'app.include_router(imports.router, prefix=API_PREFIX, dependencies=[Depends(model_center.require_import_readiness)])', code, count=1)
    if n and not re.search(r'^from fastapi import .*\bDepends\b', code, re.M):
        future = re.search(r'^from __future__ import annotations[ \t]*$', code, re.M)
        if future:
            code = code[:future.end()] + '\nfrom fastapi import Depends' + code[future.end():]
        else:
            code = 'from fastapi import Depends\n' + code
    ast.parse(code)
    return code


def parser_patch(code: str) -> str:
    if 'verified_artifacts_for_parser' in code:
        return code
    # A direct assignment of the converter options is present in SRA's Docling parser.
    match = re.search(r'^(\s*)([A-Za-z_]\w*)\s*=\s*PdfPipelineOptions\(\s*\)\s*$', code, re.M)
    if not match:
        raise IntegrationMismatch('parser.py 未找到标准 Docling PdfPipelineOptions() 构造位置；拒绝改写未知解析器')
    indent, variable = match.groups()
    add = (f'\n{indent}from .model_center import verified_artifacts_for_parser\n'
           f'{indent}_sra_models = verified_artifacts_for_parser()\n'
           f'{indent}if _sra_models is not None:\n'
           f'{indent}    {variable}.artifacts_path = _sra_models')
    code = code[:match.end()] + add + code[match.end():]
    ast.parse(code)
    return code


def namespace_of(code: str) -> str:
    found = re.search(r'\bnamespace\s+([A-Za-z_][\w.]*)\s*(?:;|\{)', code)
    if not found:
        raise IntegrationMismatch('C# 源文件缺少命名空间声明')
    return found.group(1)


def client_patch(code: str) -> str:
    if 'ModelCenterRequestAsync(' in code:
        return code
    match = re.search(r'\b(?:private|protected|internal|public)\s+(?:readonly\s+)?(?:System\.Net\.Http\.)?HttpClient\s+([A-Za-z_]\w*)', code)
    if not match:
        raise IntegrationMismatch('SraApiClient.cs 内找不到可复用的 HttpClient 字段')
    field = match.group(1)
    # Do not create unauthenticated HttpClient. Reuse existing instance used by SRA.
    # Request-specific auth headers from the existing client, if any, are reproduced.
    auth = ''
    common = re.search(r'\b(?:request|req)\.Headers\.(?:TryAddWithoutValidation|Add)\(\s*"([^"]+(?:Token|Authorization)[^"]*)"\s*,\s*([^);]+)\)', code, re.I)
    if common:
        header, expression = common.groups()
        # For request-specific signatures we only support direct field/property expressions.
        if re.fullmatch(r'(?:this\.)?_[A-Za-z_]\w*|(?:this\.)?[A-Za-z_]\w*', expression.strip()):
            auth = f'        request.Headers.TryAddWithoutValidation("{header}", {expression.strip()});\n'
    if not common and 'DefaultRequestHeaders' not in code:
        raise IntegrationMismatch('SraApiClient 使用未知鉴权方式。拒绝创建可能绕过桌面会话令牌的 API 客户端')
    snippet = f'''    // Integrated Model Center API bridge. Shares SRA's authenticated HttpClient.
    public async System.Threading.Tasks.Task<System.Text.Json.JsonElement> ModelCenterRequestAsync(
        System.Net.Http.HttpMethod method, string suffix, object? payload = null)
    {{
        using var request = new System.Net.Http.HttpRequestMessage(method,
            "/api/v1/settings/model-center" + suffix);
{auth}        if (payload is not null)
            request.Content = System.Net.Http.Json.JsonContent.Create(payload);
        using var response = await {field}.SendAsync(request).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
            throw new System.Net.Http.HttpRequestException(
                "模型中心请求失败，HTTP " + (int)response.StatusCode);
        var body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
        using var doc = System.Text.Json.JsonDocument.Parse(body);
        return doc.RootElement.Clone();
    }}
'''
    return insert_in_class(code, 'SraApiClient', snippet)


def window_entry_patch(code: str) -> str:
    if 'SraAttachModelCenterButton(' in code:
        return code
    if 'InitializeComponent();' not in code:
        raise IntegrationMismatch('MainWindow.axaml.cs 找不到 InitializeComponent()')
    code = code.replace('InitializeComponent();', 'InitializeComponent();\n        this.Opened += (_, _) => SraAttachModelCenterButton();', 1)
    snippet = '''    // Non-destructive shortcut: wraps the existing visual root instead of modifying XAML bindings.
    private void SraAttachModelCenterButton()
    {
        if (this.Content is not Avalonia.Controls.Control original) return;
        if (this.Content is Avalonia.Controls.Grid current &&
            current.Name == "SraModelCenterHost") return;
        this.Content = null;
        var host = new Avalonia.Controls.Grid { Name = "SraModelCenterHost" };
        host.Children.Add(original);
        var entry = new Avalonia.Controls.Button
        {
            Content = "⚙ 模型中心",
            HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Right,
            VerticalAlignment = Avalonia.Layout.VerticalAlignment.Bottom,
            Margin = new Avalonia.Thickness(16),
            MinWidth = 118,
        };
        entry.Click += (_, _) => SraOpenModelCenter();
        Avalonia.Controls.Panel.SetZIndex(entry, 50);
        host.Children.Add(entry);
        this.Content = host;
    }

    private void SraOpenModelCenter()
    {
        // The current VM owns the authenticated SraApiClient. Never create
        // a separate anonymous localhost client for secrets.
        object? api = null;
        foreach (var owner in new object?[] { this, DataContext })
        {
            if (owner is null) continue;
            var flags = System.Reflection.BindingFlags.Instance |
                        System.Reflection.BindingFlags.Public |
                        System.Reflection.BindingFlags.NonPublic;
            foreach (var field in owner.GetType().GetFields(flags))
            {
                try
                {
                    var value = field.GetValue(owner);
                    if (value is __CLIENT_NAMESPACE__.SraApiClient) { api = value; break; }
                }
                catch { }
            }
            if (api is not null) break;
            foreach (var property in owner.GetType().GetProperties(flags))
            {
                if (property.GetIndexParameters().Length != 0) continue;
                try
                {
                    var value = property.GetValue(owner);
                    if (value is __CLIENT_NAMESPACE__.SraApiClient)
                        { api = value; break; }
                }
                catch { }
            }
            if (api is not null) break;
        }
        if (api is __CLIENT_NAMESPACE__.SraApiClient authenticated)
            new __VIEWS_NAMESPACE__.ModelCenterWindow(authenticated).Show(this);
        else
            System.Diagnostics.Debug.WriteLine("SRA Model Center: authenticated SraApiClient not found on the active window/VM");
    }
'''
    return insert_in_class(code, 'MainWindow', snippet)


def plan(repo: Path, payload: Path) -> dict[Path, bytes]:
    files = {
        'src/sociology_research/model_center.py': None,
        'src/sociology_research/api/routes/model_center.py': None,
    }
    updates: dict[Path, bytes] = {}
    for rel in files:
        dest = repo / rel
        content = (payload / rel).read_bytes()
        if dest.is_file() and dest.read_bytes() != content:
            import hashlib
            actual = hashlib.sha256(dest.read_bytes()).hexdigest()
            if actual != KNOWN_OLD_CANDIDATE[rel]:
                raise IntegrationMismatch(f'目标已存在未知的本地版本：{rel}。拒绝覆盖用户修改')
        updates[dest] = content
    required = {
        'app': repo / 'src/sociology_research/api/app.py',
        'parser': repo / 'src/sociology_research/parser.py',
        'client': repo / 'desktop/SRA.Desktop/Services/SraApiClient.cs',
        'window': repo / 'desktop/SRA.Desktop/Views/MainWindow.axaml.cs',
    }
    for label, path in required.items():
        if not path.is_file():
            raise IntegrationMismatch(f'找不到真实 SRA 文件：{path.relative_to(repo)}')
    raw = {label: path.read_text(encoding='utf-8-sig') for label, path in required.items()}
    vm_source = repo / 'desktop/SRA.Desktop/ViewModels/MainWindowViewModel.cs'
    vm_text = vm_source.read_text(encoding='utf-8-sig') if vm_source.is_file() else ''
    if 'SraApiClient' not in (raw['window'] + vm_text):
        raise IntegrationMismatch('主窗口或 ViewModel 中找不到 SraApiClient；无法保证模型中心使用已有认证会话')
    client_ns = namespace_of(raw['client'])
    views_ns = namespace_of(raw['window'])
    # Build/validate ALL outputs before writing ANYTHING.
    for label, transform in [('app', route_patch), ('parser', parser_patch),
                             ('client', client_patch), ('window', lambda code: window_entry_patch(code).replace('__CLIENT_NAMESPACE__', client_ns).replace('__VIEWS_NAMESPACE__', views_ns))]:
        updates[required[label]] = transform(raw[label]).encode('utf8')
    window = repo / 'desktop/SRA.Desktop/Views/ModelCenterWindow.cs'
    template = (payload / 'desktop/SRA.Desktop/Views/ModelCenterWindow.cs.tmpl').read_text(encoding='utf8')
    materialized = template.replace('__CLIENT_NAMESPACE__', client_ns).replace('__VIEWS_NAMESPACE__', views_ns)
    if window.is_file() and window.read_text(encoding='utf8') != materialized:
        raise IntegrationMismatch('ModelCenterWindow.cs 已被修改，请先比较差异')
    updates[window] = materialized.encode('utf8')
    for name in ('test_model_center_core.py', 'test_model_center_routes.py'):
        src = payload / 'tests' / name
        target = repo / 'tests' / name
        if target.exists() and target.read_bytes() != src.read_bytes():
            raise IntegrationMismatch('测试文件已有本地改动：' + name)
        updates[target] = src.read_bytes()
    return updates


def main() -> int:
    arg = argparse.ArgumentParser(description='Safely integrate SRA Model Center into the actual repository')
    arg.add_argument('--repo', type=Path, required=True)
    arg.add_argument('--apply', action='store_true', help='write source changes after validation; default is dry-run')
    args = arg.parse_args()
    root = args.repo.resolve()
    payload = Path(__file__).resolve().parents[1]
    try:
        outputs = plan(root, payload)
        changed = {p: c for p, c in outputs.items() if not p.is_file() or p.read_bytes() != c}
        print(f'预检通过：计划检查 {len(outputs)} 个文件，实际修改/新增 {len(changed)} 个文件')
        for filename in changed:
            print('  ', filename.relative_to(root))
        if not args.apply:
            print('未写入任何文件。执行时加 --apply。')
            return 0
        backup_root = root.parent / 'SRA-model-center-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        original = {}
        try:
            for target, content in changed.items():
                relative = target.relative_to(root)
                before = target.read_bytes() if target.is_file() else None
                original[target] = before
                if before is not None:
                    backup = backup_root / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(before)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        except Exception:
            for path, before in original.items():
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(before)
            raise
        print('已修改真实工程源码；原始文件备份：', backup_root)
        print('请运行项目 pytest 和 dotnet build；不要直接发布 Release。')
        return 0
    except (OSError, IntegrationMismatch, SyntaxError) as exc:
        print('安全预检失败：', exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
