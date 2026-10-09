using System.Text.Json;

namespace SRA.Desktop.Services;

public sealed class DesktopPreferences
{
    public string ThemeMode { get; set; } = "跟随系统";
    public bool ShowInspector { get; set; } = true;
    public bool CheckForUpdatesAtStartup { get; set; } = true;
    public bool IncludePrereleaseUpdates { get; set; } = false;
}

public sealed class DesktopPreferencesService
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
    private readonly string _path;

    public DesktopPreferencesService()
    {
        var root = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var directory = Path.Combine(root, "SRA");
        Directory.CreateDirectory(directory);
        _path = Path.Combine(directory, "desktop-preferences.json");
    }

    public DesktopPreferences Load()
    {
        try
        {
            if (!File.Exists(_path)) return new DesktopPreferences();
            return JsonSerializer.Deserialize<DesktopPreferences>(File.ReadAllText(_path)) ?? new DesktopPreferences();
        }
        catch
        {
            return new DesktopPreferences();
        }
    }

    public void Save(DesktopPreferences preferences)
    {
        try
        {
            File.WriteAllText(_path, JsonSerializer.Serialize(preferences, JsonOptions));
        }
        catch
        {
            // Desktop preferences are convenience state. Never block the research workflow.
        }
    }
}
