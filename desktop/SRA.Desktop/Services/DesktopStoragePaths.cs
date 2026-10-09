namespace SRA.Desktop.Services;

/// <summary>
/// Known SRA per-user paths, independent of the install directory. On macOS
/// they match Python's ~/Library/Application Support/SRA, not .NET's generic
/// LocalApplicationData fallback. SRA_HOME is an explicit developer override.
/// </summary>
public static class DesktopStoragePaths
{
    public static string UserRoot
    {
        get
        {
            var custom = Environment.GetEnvironmentVariable("SRA_HOME");
            if (!string.IsNullOrWhiteSpace(custom)) return Path.GetFullPath(custom);
            if (OperatingSystem.IsMacOS())
                return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    "Library", "Application Support", "SRA");
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SRA");
        }
    }

    public static string Data => Path.Combine(UserRoot, "data");
    public static string Config => Path.Combine(UserRoot, "config");
    public static string Cache => Path.Combine(UserRoot, "cache");
}
