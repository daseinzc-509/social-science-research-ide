using System.Diagnostics;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Security.Cryptography;

namespace SRA.Desktop.Services;

/// <summary>
/// Owns one private Python backend child process in an installed app.
/// Developer builds without a bundled executable keep the legacy port 8766.
/// The session token is ephemeral and exists only in the parent/child memory.
/// </summary>
public sealed class LocalBackendHost : IDisposable
{
    private readonly Process? _owned;
    private readonly CancellationTokenSource _shutdown = new();
    private readonly Queue<string> _diagnostics = new();
    private readonly object _lock = new();

    private LocalBackendHost(Uri baseUri, string? token, Process? owned, Task? initialFailure = null)
    {
        BaseUri = baseUri;
        SessionToken = token;
        _owned = owned;
        ReadyTask = initialFailure ?? (owned is null
            ? Task.CompletedTask
            : WaitUntilReadyAsync(_shutdown.Token));
    }

    public Uri BaseUri { get; }
    public string? SessionToken { get; }
    public Task ReadyTask { get; }
    public bool IsBundled => _owned is not null;

    public static LocalBackendHost Launch()
    {
        string? backendExecutable = FindBundledExecutable();
        if (backendExecutable is null)
        {
            // Developer checkout: the local user still explicitly starts `sra api`.
            return new LocalBackendHost(new Uri("http://127.0.0.1:8766/"), null, null);
        }

        int port = ChooseLoopbackPort();
        string token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
        var baseUri = new Uri($"http://127.0.0.1:{port}/");
        try
        {
            var info = new ProcessStartInfo(backendExecutable)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                WorkingDirectory = Path.GetDirectoryName(backendExecutable)!,
            };
            info.ArgumentList.Add("--port");
            info.ArgumentList.Add(port.ToString(System.Globalization.CultureInfo.InvariantCulture));
            info.Environment["SRA_DESKTOP_TOKEN"] = token;
            // Confine caches we own to the same per-user SRA root. Respect
            // explicit user overrides and never redirect developer-mode APIs.
            var cacheRoot = DesktopStoragePaths.Cache;
            Directory.CreateDirectory(cacheRoot);
            SetEnvIfMissing(info, "HF_HOME", Path.Combine(cacheRoot, "huggingface"));
            SetEnvIfMissing(info, "TORCH_HOME", Path.Combine(cacheRoot, "torch"));
            SetEnvIfMissing(info, "XDG_CACHE_HOME", Path.Combine(cacheRoot, "xdg"));
            // DOCLING_ARTIFACTS_PATH is intentionally not forced to an empty
            // folder: it may require pre-fetched model layout and break OCR.

            // The backend resolves its own per-user private storage directory.
            // Never point it at a Program Files/.app resource folder.
            var process = new Process { StartInfo = info, EnableRaisingEvents = true };
            if (!process.Start()) throw new InvalidOperationException("Bundled backend did not start");
            var host = new LocalBackendHost(baseUri, token, process);
            process.OutputDataReceived += (_, eventArgs) => host.RecordDiagnostic(eventArgs.Data);
            process.ErrorDataReceived += (_, eventArgs) => host.RecordDiagnostic(eventArgs.Data);
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            // Ownership is transferred; do not dispose process at the end of this method.
            return host;
        }
        catch (Exception ex)
        {
            return new LocalBackendHost(baseUri, token, null,
                Task.FromException(new InvalidOperationException("Unable to launch the bundled Python backend: " + ex.Message)));
        }
    }

    private static void SetEnvIfMissing(ProcessStartInfo info, string name, string value)
    {
        if (!info.Environment.TryGetValue(name, out var existing) || string.IsNullOrWhiteSpace(existing))
            info.Environment[name] = value;
    }

    private static string? FindBundledExecutable()
    {
        string programDirectory = AppContext.BaseDirectory;
        string binary = OperatingSystem.IsWindows() ? "sra-backend.exe" : "sra-backend";
        var candidates = OperatingSystem.IsMacOS()
            ? new[]
            {
                Path.GetFullPath(Path.Combine(programDirectory, "..", "Resources", "backend", "sra-backend", binary)),
                Path.Combine(programDirectory, "backend", "sra-backend", binary),
            }
            : new[] { Path.Combine(programDirectory, "backend", "sra-backend", binary) };
        return candidates.FirstOrDefault(File.Exists);
    }

    private static int ChooseLoopbackPort()
    {
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        return ((IPEndPoint)listener.LocalEndpoint).Port;
    }

    private void RecordDiagnostic(string? message)
    {
        if (string.IsNullOrWhiteSpace(message)) return;
        lock (_lock)
        {
            _diagnostics.Enqueue(message.Length > 350 ? message[..350] : message);
            while (_diagnostics.Count > 8) _diagnostics.Dequeue();
        }
    }

    private async Task WaitUntilReadyAsync(CancellationToken cancellationToken)
    {
        using var probe = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
        if (!string.IsNullOrEmpty(SessionToken))
            probe.DefaultRequestHeaders.Add("X-SRA-Desktop-Token", SessionToken);

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromMinutes(3));
        while (!timeout.IsCancellationRequested)
        {
            if (_owned is null || _owned.HasExited)
            {
                string lastMessage;
                lock (_lock) lastMessage = _diagnostics.Count == 0 ? "No backend diagnostics were emitted." : _diagnostics.Last();
                throw new InvalidOperationException("Python backend exited before it became ready. " + lastMessage);
            }
            try
            {
                using var response = await probe.GetAsync(new Uri(BaseUri, "api/v1/health"), timeout.Token);
                if (response.IsSuccessStatusCode) return;
                if (response.StatusCode == HttpStatusCode.Unauthorized)
                    throw new InvalidOperationException("Bundled backend session authentication failed");
            }
            catch (HttpRequestException) { }
            catch (TaskCanceledException) when (!timeout.IsCancellationRequested) { }
            try { await Task.Delay(300, timeout.Token); }
            catch (OperationCanceledException) when (timeout.IsCancellationRequested && !cancellationToken.IsCancellationRequested) { break; }
        }
        cancellationToken.ThrowIfCancellationRequested();
        throw new TimeoutException("Python backend did not become ready within three minutes");
    }

    public void Dispose()
    {
        _shutdown.Cancel();
        if (_owned is not null)
        {
            try
            {
                if (!_owned.HasExited) _owned.Kill(entireProcessTree: true);
            }
            catch (InvalidOperationException) { }
            catch (System.ComponentModel.Win32Exception) { }
            _owned.Dispose();
        }
        _shutdown.Dispose();
    }
}
