using System.Net.Http.Headers;
using System.Reflection;
using System.Text.Json;

namespace SRA.Desktop.Services;

/// <summary>
/// Public GitHub Release discovery only. No credentials, document text, or
/// private research metadata are sent. This never runs downloaded binaries.
/// </summary>
public sealed class DesktopUpdateService
{
    private const string Repository = "daseinzc-509/social-science-research-ide";
    private static readonly Uri ReleasesApi =
        new($"https://api.github.com/repos/{Repository}/releases?per_page=30");
    private static readonly string ExpectedReleasePrefix =
        $"https://github.com/{Repository}/releases/tag/";

    private static readonly HttpClient Http = CreateClient();

    public Version InstalledVersion { get; } =
        typeof(DesktopUpdateService).Assembly.GetName().Version ?? new Version(0, 1, 0, 0);

    private static readonly string InstalledTag =
        typeof(DesktopUpdateService).Assembly
            .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?
            .InformationalVersion.Split('+')[0]
        ?? (typeof(DesktopUpdateService).Assembly.GetName().Version?.ToString(3) ?? "0.1.0");

    public string InstalledVersionLabel => $"v{InstalledTag.TrimStart('v', 'V')}";

    private static HttpClient CreateClient()
    {
        var http = new HttpClient { Timeout = TimeSpan.FromSeconds(8) };
        http.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("SRA-Desktop", "0.1"));
        http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        return http;
    }

    public async Task<DesktopRelease?> FindUpdateAsync(
        bool includePrereleases,
        CancellationToken cancellationToken = default)
    {
        using var response = await Http.GetAsync(ReleasesApi, cancellationToken);
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var releases = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
        if (releases.RootElement.ValueKind != JsonValueKind.Array)
            throw new InvalidOperationException("GitHub Releases returned an invalid response.");

        DesktopRelease? newest = null;
        foreach (var release in releases.RootElement.EnumerateArray())
        {
            if (release.ValueKind != JsonValueKind.Object ||
                ReadBoolean(release, "draft") ||
                (ReadBoolean(release, "prerelease") && !includePrereleases))
                continue;

            var tag = ReadString(release, "tag_name");
            var url = ReadString(release, "html_url");
            var version = ParseVersion(tag);
            if (version is null || CompareTags(tag, InstalledTag) <= 0 ||
                !Uri.TryCreate(url, UriKind.Absolute, out var page) ||
                page.Scheme != Uri.UriSchemeHttps ||
                !page.AbsoluteUri.StartsWith(ExpectedReleasePrefix, StringComparison.OrdinalIgnoreCase))
                continue;

            var candidate = new DesktopRelease(
                tag!, page, ReadBoolean(release, "prerelease"), version);
            if (newest is null || CompareTags(candidate.Tag, newest.Tag) > 0)
                newest = candidate;
        }
        return newest;
    }

    // Git tags such as v0.2.0-alpha.2 must update v0.2.0-alpha.1.
    // System.Version alone drops the prerelease component and misses that update.
    public static int CompareTags(string? left, string? right)
    {
        var leftVersion = ParseVersion(left);
        var rightVersion = ParseVersion(right);
        if (leftVersion is null || rightVersion is null) return 0;
        int core = leftVersion.CompareTo(rightVersion);
        if (core != 0) return core;
        string? leftSuffix = Suffix(left);
        string? rightSuffix = Suffix(right);
        if (leftSuffix is null && rightSuffix is null) return 0;
        if (leftSuffix is null) return 1; // Stable is newer than prerelease.
        if (rightSuffix is null) return -1;
        var a = leftSuffix.Split('.');
        var b = rightSuffix.Split('.');
        for (int i = 0; i < Math.Min(a.Length, b.Length); i++)
        {
            bool aNum = int.TryParse(a[i], out int aNumber);
            bool bNum = int.TryParse(b[i], out int bNumber);
            int cmp = aNum && bNum ? aNumber.CompareTo(bNumber)
                : aNum ? -1 : bNum ? 1
                : string.CompareOrdinal(a[i], b[i]);
            if (cmp != 0) return cmp;
        }
        return a.Length.CompareTo(b.Length);
    }

    private static string? Suffix(string? tag)
    {
        if (tag is null) return null;
        int at = tag.IndexOf('-');
        if (at < 0) return null;
        string suffix = tag[(at + 1)..].Split('+')[0];
        return suffix.Length == 0 ? null : suffix;
    }

    public static Version? ParseVersion(string? tag)
    {
        if (string.IsNullOrWhiteSpace(tag)) return null;
        var text = tag.Trim().TrimStart('v', 'V');
        var separator = text.IndexOfAny(['-', '+']);
        if (separator >= 0) text = text[..separator];
        return Version.TryParse(text, out var version) && version.Build >= 0
            ? version
            : null;
    }

    private static bool ReadBoolean(JsonElement json, string key) =>
        json.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.True;

    private static string? ReadString(JsonElement json, string key) =>
        json.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;
}

public sealed record DesktopRelease(string Tag, Uri PageUri, bool Prerelease, Version Version);
