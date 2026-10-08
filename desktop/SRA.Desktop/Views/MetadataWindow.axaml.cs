using System.Text.Json;
using Avalonia.Controls;
using Avalonia.Interactivity;
using SRA.Desktop.Models;
using SRA.Desktop.Services;

namespace SRA.Desktop.Views;

public partial class MetadataWindow : Window
{
    private readonly SraApiClient? _api;
    private readonly string _paperId = "";

    public MetadataWindow()
    {
        InitializeComponent();
    }

    public MetadataWindow(SraApiClient api, string paperId, PaperDetailResponse detail) : this()
    {
        _api = api;
        _paperId = paperId;
        Populate(detail);
    }

    private void Populate(PaperDetailResponse detail)
    {
        if (detail.Card is { ValueKind: JsonValueKind.Object } card &&
            card.TryGetProperty("metadata", out var metadata) && metadata.ValueKind == JsonValueKind.Object)
        {
            TitleBox.Text = JsonRead.Text(metadata, "title");
            AuthorsBox.Text = string.Join("、", JsonRead.StringList(metadata, "authors"));
            JournalBox.Text = JsonRead.Text(metadata, "journal");
            YearBox.Text = JsonRead.Text(metadata, "year");
            VolumeBox.Text = JsonRead.Text(metadata, "volume");
            IssueBox.Text = JsonRead.Text(metadata, "issue");
            PagesBox.Text = JsonRead.Text(metadata, "pages");
            DoiBox.Text = JsonRead.Text(metadata, "doi");
            KeywordsBox.Text = string.Join("；", JsonRead.StringList(metadata, "keywords"));
        }
        if (detail.MetadataOverrides.ValueKind == JsonValueKind.Object)
        {
            SourceBox.Text = JsonRead.Text(detail.MetadataOverrides, "source_note");
        }
    }

    private async void Save_Click(object? sender, RoutedEventArgs e)
    {
        if (_api is null)
        {
            return;
        }
        try
        {
            StatusText.Text = "正在保存…";
            await _api.SaveMetadataAsync(
                _paperId,
                new MetadataOverrideUpdate
                {
                    Values = new Dictionary<string, object?>
                    {
                        ["title"] = Clean(TitleBox.Text),
                        ["authors"] = Clean(AuthorsBox.Text),
                        ["journal"] = Clean(JournalBox.Text),
                        ["year"] = Clean(YearBox.Text),
                        ["volume"] = Clean(VolumeBox.Text),
                        ["issue"] = Clean(IssueBox.Text),
                        ["pages"] = Clean(PagesBox.Text),
                        ["doi"] = Clean(DoiBox.Text),
                        ["keywords"] = Clean(KeywordsBox.Text),
                    },
                    SourceNote = Clean(SourceBox.Text) as string,
                }
            );
            Close(true);
        }
        catch (Exception exc)
        {
            StatusText.Text = exc.Message;
        }
    }

    private async void Reset_Click(object? sender, RoutedEventArgs e)
    {
        if (_api is null)
        {
            return;
        }
        try
        {
            StatusText.Text = "正在恢复自动提取…";
            await _api.ResetMetadataAsync(_paperId);
            Close(true);
        }
        catch (Exception exc)
        {
            StatusText.Text = exc.Message;
        }
    }

    private void Cancel_Click(object? sender, RoutedEventArgs e) => Close(false);

    private static object? Clean(string? value)
    {
        var text = (value ?? "").Trim();
        return text.Length == 0 ? null : text;
    }
}
