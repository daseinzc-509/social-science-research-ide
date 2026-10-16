"""Source-tree integration tests: generation, rollback, version mismatch, idempotence."""
import ast
import importlib.util
import re
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / 'scripts/apply_model_center_integration.py'
spec = importlib.util.spec_from_file_location('sra_integrator', S)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@pytest.fixture
def repo(tmp_path):
    def write(path, text):
        p=tmp_path/path
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(text,encoding='utf8')
    app = """from __future__ import annotations
from fastapi import FastAPI
from .routes import settings, imports
API_PREFIX = "/api/v1"

def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(imports.router, prefix=API_PREFIX)
    app.include_router(settings.router, prefix=API_PREFIX)
    return app
"""
    write('src/sociology_research/api/app.py', app)
    write('src/sociology_research/parser.py', '''from docling.datamodel.pipeline_options import PdfPipelineOptions\nclass DoclingDocumentParser:\n def parse(self):\n  options = PdfPipelineOptions()\n  options.do_ocr = True\n  return options\n''')
    write('desktop/SRA.Desktop/Services/SraApiClient.cs', '''using System.Net.Http;\nnamespace SRA.Desktop.Services;\npublic sealed class SraApiClient {\n  private readonly HttpClient _httpClient;\n  public SraApiClient(HttpClient client){ _httpClient = client; _httpClient.DefaultRequestHeaders.Add("X-SRA-Session-Token", "test"); }\n}\n''')
    write('desktop/SRA.Desktop/ViewModels/MainWindowViewModel.cs', 'namespace SRA.Desktop.ViewModels; public class MainWindowViewModel { private SraApiClient _apiClient; }')
    write('desktop/SRA.Desktop/Views/MainWindow.axaml.cs', '''using Avalonia.Controls;\nnamespace SRA.Desktop.Views;\npublic partial class MainWindow : Window {\n  public MainWindow() { InitializeComponent(); }\n  private void InitializeComponent() {}\n}\n''')
    return tmp_path


def test_writes_and_wires_real_layers(repo):
    items = mod.plan(repo, ROOT)
    assert len(items) == 9
    api = items[repo/'src/sociology_research/api/app.py'].decode()
    assert 'model_center.router, prefix=API_PREFIX' in api
    assert 'model_center_core.prepare_runtime()' in api
    assert 'model_center.require_import_readiness' in api
    ast.parse(api)
    parser = items[repo/'src/sociology_research/parser.py'].decode()
    assert 'options.artifacts_path = _sra_models' in parser
    ast.parse(parser)
    cs = items[repo/'desktop/SRA.Desktop/Services/SraApiClient.cs'].decode()
    assert 'ModelCenterRequestAsync' in cs and '_httpClient.SendAsync(request)' in cs
    entry = items[repo/'desktop/SRA.Desktop/Views/MainWindow.axaml.cs'].decode()
    assert 'SraOpenModelCenter' in entry
    assert '__CLIENT_NAMESPACE__' not in entry
    view = items[repo/'desktop/SRA.Desktop/Views/ModelCenterWindow.cs'].decode()
    assert 'SRA.Desktop.Services' in view and 'SRA.Desktop.Views' in view
    assert 'OpenAI Compatible Base URL' in view


def test_idempotence(repo):
    first = mod.plan(repo, ROOT)
    for path, contents in first.items():
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(contents)
    second = mod.plan(repo, ROOT)
    assert all(p.read_bytes() == contents for p,contents in second.items())


def test_unknown_parser_rejected_atomically(repo):
    parser=repo/'src/sociology_research/parser.py'
    parser.write_text('broken_parser()', encoding='utf8')
    before={p:p.read_bytes() for p in repo.rglob('*') if p.is_file()}
    with pytest.raises(mod.IntegrationMismatch, match='PdfPipelineOptions'):
        mod.plan(repo, ROOT)
    assert all(p.read_bytes()==content for p, content in before.items())
    assert not (repo/'desktop/SRA.Desktop/Views/ModelCenterWindow.cs').exists()


def test_unknown_authenticated_client_rejected(repo):
    client=repo/'desktop/SRA.Desktop/Services/SraApiClient.cs'
    client.write_text('namespace SRA.Desktop.Services;\npublic class SraApiClient {}',encoding='utf8')
    with pytest.raises(mod.IntegrationMismatch, match='HttpClient'):
        mod.plan(repo, ROOT)


def test_brace_scanner_with_strings():
    code='''namespace App; public class SraApiClient { string s="}"; /* { */ void Go(){ var x="{"; } }'''
    assert mod.insert_in_class(code, 'SraApiClient', 'void Test(){}').count('void Test()') == 1


def test_cli_can_apply_and_rerun(repo, capsys):
    import subprocess, sys
    cmd = [sys.executable, str(S), '--repo', str(repo)]
    before = (repo/'src/sociology_research/api/app.py').read_bytes()
    dry = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert dry.returncode == 0, dry.stderr
    assert (repo/'src/sociology_research/api/app.py').read_bytes() == before
    applied = subprocess.run([*cmd, '--apply'], capture_output=True, text=True, check=False)
    assert applied.returncode == 0, applied.stderr
    assert (repo/'desktop/SRA.Desktop/Views/ModelCenterWindow.cs').is_file()
    assert (repo/'src/sociology_research/api/app.py').read_bytes() != before
    again = subprocess.run([*cmd, '--apply'], capture_output=True, text=True, check=False)
    assert again.returncode == 0 and '实际修改/新增 0 个文件' in again.stdout
