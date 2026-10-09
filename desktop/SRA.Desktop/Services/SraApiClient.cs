using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using SRA.Desktop.Models;

namespace SRA.Desktop.Services;

public sealed class SraApiClient : IDisposable
{
    private readonly HttpClient _http;
    private readonly List<string> _temporaryPdfs = [];
    private readonly JsonSerializerOptions _jsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public SraApiClient(string baseUrl, string? desktopSessionToken = null)
    {
        if (!Uri.TryCreate(baseUrl, UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https"))
        {
            throw new ArgumentException("SRA API base URL must be an absolute HTTP(S) URL.", nameof(baseUrl));
        }

        BaseUri = new Uri(uri.ToString().TrimEnd('/') + "/", UriKind.Absolute);
        _http = new HttpClient
        {
            BaseAddress = BaseUri,
            Timeout = TimeSpan.FromMinutes(2),
        };
        if (!string.IsNullOrEmpty(desktopSessionToken))
            _http.DefaultRequestHeaders.Add("X-SRA-Desktop-Token", desktopSessionToken);
    }

    public Uri BaseUri { get; }
    public Uri DocsUri => new(BaseUri, "docs");
    public Uri PaperPdfUri(string paperId) => new(BaseUri, $"api/v1/papers/{Uri.EscapeDataString(paperId)}/pdf");

    /// <summary>
    /// The bundled backend requires a secret header for PDF access. An external
    /// PDF viewer cannot add that header, so save a short-lived user-only temp
    /// copy and open the local file instead of leaking a bearer token in a URL.
    /// </summary>
    public async Task<string> DownloadPdfForOpenAsync(string paperId, CancellationToken cancellationToken = default)
    {
        var folder = Path.Combine(Path.GetTempPath(), "SRA-PDF-Viewer", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(folder);
        if (!OperatingSystem.IsWindows())
            new DirectoryInfo(folder).UnixFileMode =
                UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute;
        var destination = Path.Combine(folder, "paper.pdf");
        try
        {
            using var response = await _http.GetAsync(
                $"api/v1/papers/{Uri.EscapeDataString(paperId)}/pdf",
                HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            await EnsureSuccessAsync(response, cancellationToken);
            await using (var output = File.Create(destination))
            {
                await response.Content.CopyToAsync(output, cancellationToken);
            }
            _temporaryPdfs.Add(folder);
            return destination;
        }
        catch
        {
            try { Directory.Delete(folder, recursive: true); } catch (IOException) { }
            throw;
        }
    }

    public Task<HealthResponse> GetHealthAsync(CancellationToken cancellationToken = default) =>
        GetJsonAsync<HealthResponse>("api/v1/health", cancellationToken);

    public async Task<IReadOnlyList<PaperSummary>> GetPapersAsync(CancellationToken cancellationToken = default)
    {
        var response = await GetJsonAsync<PaperListResponse>("api/v1/papers", cancellationToken);
        return response.Papers;
    }

    public Task<PaperDetailResponse> GetPaperAsync(string paperId, CancellationToken cancellationToken = default) =>
        GetJsonAsync<PaperDetailResponse>($"api/v1/papers/{Uri.EscapeDataString(paperId)}", cancellationToken);

    public Task<JsonElement> GetDashboardAsync(CancellationToken cancellationToken = default) =>
        GetJsonAsync<JsonElement>("api/v1/dashboard", cancellationToken);

    public Task<JsonElement> GetDoctorAsync(bool deep = false, CancellationToken cancellationToken = default) =>
        GetJsonAsync<JsonElement>($"api/v1/maintenance/doctor?deep={(deep ? "true" : "false")}", cancellationToken);

    public async Task<JsonElement> PruneCacheAsync(bool apply, bool vacuum, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsJsonAsync(
            "api/v1/maintenance/prune-cache",
            new { all_runs = false, apply, vacuum },
            _jsonOptions,
            cancellationToken
        );
        return await ReadJsonAsync<JsonElement>(response, cancellationToken);
    }

    public async Task<string> GetReadingReportAsync(string paperId, CancellationToken cancellationToken = default) =>
        await GetTextAsync($"api/v1/papers/{Uri.EscapeDataString(paperId)}/reading-report", cancellationToken);

    public async Task<string> GetCardJsonAsync(string paperId, CancellationToken cancellationToken = default) =>
        await GetTextAsync($"api/v1/papers/{Uri.EscapeDataString(paperId)}/card.json", cancellationToken);

    public async Task<string> GetParsedTextAsync(string paperId, CancellationToken cancellationToken = default) =>
        await GetTextAsync($"api/v1/papers/{Uri.EscapeDataString(paperId)}/text.txt", cancellationToken);

    public async Task<string> GetReferencesJsonAsync(string paperId, CancellationToken cancellationToken = default) =>
        await GetTextAsync($"api/v1/papers/{Uri.EscapeDataString(paperId)}/references.json", cancellationToken);

    public async Task<string> GetReferencesBibtexAsync(string paperId, CancellationToken cancellationToken = default) =>
        await GetTextAsync($"api/v1/papers/{Uri.EscapeDataString(paperId)}/references.bib", cancellationToken);

    public async Task<JobAccepted> StartAnalysisAsync(string paperId, AnalysisRequest request, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsJsonAsync(
            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/analyze",
            request,
            _jsonOptions,
            cancellationToken
        );
        return await ReadJsonAsync<JobAccepted>(response, cancellationToken);
    }

    public async Task<JobAccepted> StartBatchAnalysisAsync(IEnumerable<string>? paperIds = null, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsJsonAsync(
            "api/v1/batch-analyze",
            new { paper_ids = paperIds?.ToArray() },
            _jsonOptions,
            cancellationToken
        );
        return await ReadJsonAsync<JobAccepted>(response, cancellationToken);
    }

    public async Task<JobAccepted> StartReparseAsync(string paperId, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsync(
            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/reparse",
            content: null,
            cancellationToken
        );
        return await ReadJsonAsync<JobAccepted>(response, cancellationToken);
    }

    public async Task<JsonElement> ExtractReferencesAsync(string paperId, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsync(
            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/references/extract",
            content: null,
            cancellationToken
        );
        return await ReadJsonAsync<JsonElement>(response, cancellationToken);
    }

    public async Task<JsonElement> ExtractReferencesBatchAsync(IEnumerable<string>? paperIds = null, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsJsonAsync(
            "api/v1/references/extract-batch",
            new { paper_ids = paperIds?.ToArray() },
            _jsonOptions,
            cancellationToken
        );
        return await ReadJsonAsync<JsonElement>(response, cancellationToken);
    }

    public async Task<JsonElement> SaveMetadataAsync(string paperId, MetadataOverrideUpdate update, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsJsonAsync(
            $"api/v1/papers/{Uri.EscapeDataString(paperId)}/metadata",
            update,
            _jsonOptions,
            cancellationToken
        );
        return await ReadJsonAsync<JsonElement>(response, cancellationToken);
    }

    public async Task ResetMetadataAsync(string paperId, CancellationToken cancellationToken = default)
    {
        using var response = await _http.DeleteAsync($"api/v1/papers/{Uri.EscapeDataString(paperId)}/metadata", cancellationToken);
        await EnsureSuccessAsync(response, cancellationToken);
    }

    public async Task<DeletePaperResult> DeletePaperAsync(string paperId, bool deleteFile = true, CancellationToken cancellationToken = default)
    {
        using var request = new HttpRequestMessage(HttpMethod.Delete, $"api/v1/papers/{Uri.EscapeDataString(paperId)}")
        {
            Content = JsonContent.Create(new { delete_file = deleteFile }, options: _jsonOptions),
        };
        using var response = await _http.SendAsync(request, cancellationToken);
        return await ReadJsonAsync<DeletePaperResult>(response, cancellationToken);
    }

    public async Task<JobAccepted> ImportPaperAsync(Stream pdfStream, string fileName, CancellationToken cancellationToken = default)
    {
        using var form = new MultipartFormDataContent();
        using var file = new StreamContent(pdfStream);
        file.Headers.ContentType = new MediaTypeHeaderValue("application/pdf");
        form.Add(file, "file", string.IsNullOrWhiteSpace(fileName) ? "upload.pdf" : fileName);

        using var response = await _http.PostAsync("api/v1/papers/import", form, cancellationToken);
        return await ReadJsonAsync<JobAccepted>(response, cancellationToken);
    }

    public async Task<JobAccepted> ImportPapersBatchAsync(IReadOnlyList<(Stream Stream, string FileName)> files, CancellationToken cancellationToken = default)
    {
        using var form = new MultipartFormDataContent();
        var contents = new List<StreamContent>();
        try
        {
            foreach (var (stream, fileName) in files)
            {
                var content = new StreamContent(stream);
                contents.Add(content);
                content.Headers.ContentType = new MediaTypeHeaderValue("application/pdf");
                form.Add(content, "files", string.IsNullOrWhiteSpace(fileName) ? "upload.pdf" : fileName);
            }
            using var response = await _http.PostAsync("api/v1/papers/import-batch", form, cancellationToken);
            return await ReadJsonAsync<JobAccepted>(response, cancellationToken);
        }
        finally
        {
            foreach (var content in contents)
            {
                content.Dispose();
            }
        }
    }

    public Task<JobSnapshot> GetJobAsync(string jobId, CancellationToken cancellationToken = default) =>
        GetJsonAsync<JobSnapshot>($"api/v1/jobs/{Uri.EscapeDataString(jobId)}", cancellationToken);

    public async Task<JobSnapshot> WaitForJobAsync(
        string jobId,
        Action<JobSnapshot>? onProgress = null,
        CancellationToken cancellationToken = default
    )
    {
        var started = DateTimeOffset.UtcNow;
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var snapshot = await GetJobAsync(jobId, cancellationToken);
            onProgress?.Invoke(snapshot);

            if (snapshot.Status == "done")
            {
                return snapshot;
            }
            if (snapshot.Status == "error")
            {
                throw new SraApiException(snapshot.Error ?? "SRA background job failed.");
            }
            if (DateTimeOffset.UtcNow - started > TimeSpan.FromMinutes(20))
            {
                throw new TimeoutException("SRA background job did not finish within 20 minutes.");
            }
            await Task.Delay(750, cancellationToken);
        }
    }

    public Task<ModelSettingsView> GetModelSettingsAsync(CancellationToken cancellationToken = default) =>
        GetJsonAsync<ModelSettingsView>("api/v1/settings/models", cancellationToken);

    public async Task<ModelSettingsView> UpdateModelSettingsAsync(ModelSettingsUpdate update, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PutAsJsonAsync("api/v1/settings/models", update, _jsonOptions, cancellationToken);
        return await ReadJsonAsync<ModelSettingsView>(response, cancellationToken);
    }

    private async Task<string> GetTextAsync(string relativeUrl, CancellationToken cancellationToken)
    {
        using var response = await _http.GetAsync(relativeUrl, cancellationToken);
        await EnsureSuccessAsync(response, cancellationToken);
        return await response.Content.ReadAsStringAsync(cancellationToken);
    }

    private async Task<T> GetJsonAsync<T>(string relativeUrl, CancellationToken cancellationToken)
    {
        using var response = await _http.GetAsync(relativeUrl, cancellationToken);
        return await ReadJsonAsync<T>(response, cancellationToken);
    }

    private async Task<T> ReadJsonAsync<T>(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        await EnsureSuccessAsync(response, cancellationToken);
        var value = await response.Content.ReadFromJsonAsync<T>(_jsonOptions, cancellationToken);
        return value ?? throw new SraApiException("SRA API returned an empty JSON response.");
    }

    private static async Task EnsureSuccessAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }

        var text = await response.Content.ReadAsStringAsync(cancellationToken);
        var detail = text;
        try
        {
            using var document = JsonDocument.Parse(text);
            if (document.RootElement.ValueKind == JsonValueKind.Object &&
                document.RootElement.TryGetProperty("detail", out var node) &&
                node.ValueKind == JsonValueKind.String)
            {
                detail = node.GetString() ?? text;
            }
        }
        catch (JsonException)
        {
        }

        throw new SraApiException($"SRA API {(int)response.StatusCode}: {detail}".Trim());
    }

    public void Dispose()
    {
        _http.Dispose();
        foreach (var folder in _temporaryPdfs)
        {
            try { Directory.Delete(folder, recursive: true); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
        _temporaryPdfs.Clear();
    }
}

public sealed class SraApiException(string message) : Exception(message);
