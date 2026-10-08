using System.Text.Json;
using System.Text.Json.Serialization;

namespace SRA.Desktop.Models;

public sealed class HealthResponse
{
    [JsonPropertyName("status")]
    public string Status { get; init; } = "";

    [JsonPropertyName("service")]
    public string Service { get; init; } = "";

    [JsonPropertyName("api_version")]
    public string ApiVersion { get; init; } = "";
}

public sealed class JobAccepted
{
    [JsonPropertyName("job_id")]
    public string JobId { get; init; } = "";
}

public sealed class JobSnapshot
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = "";

    [JsonPropertyName("kind")]
    public string Kind { get; init; } = "";

    [JsonPropertyName("status")]
    public string Status { get; init; } = "";

    [JsonPropertyName("messages")]
    public List<string> Messages { get; init; } = [];

    [JsonPropertyName("result")]
    public JsonElement? Result { get; init; }

    [JsonPropertyName("error")]
    public string? Error { get; init; }

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; init; } = "";

    [JsonPropertyName("started_at")]
    public string? StartedAt { get; init; }

    [JsonPropertyName("finished_at")]
    public string? FinishedAt { get; init; }

    [JsonIgnore]
    public bool IsFinished => Status is "done" or "error";

    public string? ResultString(string name)
    {
        if (Result is not { ValueKind: JsonValueKind.Object } result ||
            !result.TryGetProperty(name, out var property))
        {
            return null;
        }
        return property.ValueKind == JsonValueKind.String ? property.GetString() : property.ToString();
    }
}

public sealed class PaperSummary
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = "";

    [JsonPropertyName("original_name")]
    public string OriginalName { get; init; } = "";

    [JsonPropertyName("page_count")]
    public int PageCount { get; init; }

    [JsonPropertyName("parse_status")]
    public string ParseStatus { get; init; } = "";

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; init; } = "";

    [JsonPropertyName("has_card")]
    public bool HasCard { get; init; }

    [JsonPropertyName("card_error")]
    public string? CardError { get; init; }

    [JsonPropertyName("title")]
    public string Title { get; init; } = "";

    [JsonPropertyName("authors")]
    public List<string> Authors { get; init; } = [];

    [JsonPropertyName("year")]
    public int? Year { get; init; }

    [JsonPropertyName("journal")]
    public string? Journal { get; init; }

    [JsonPropertyName("facts")]
    public int Facts { get; init; }

    [JsonPropertyName("evidence_spans")]
    public int EvidenceSpans { get; init; }

    [JsonPropertyName("analysis_claims")]
    public int AnalysisClaims { get; init; }

    [JsonPropertyName("warnings")]
    public int Warnings { get; init; }

    [JsonIgnore]
    public string AuthorLine => Authors.Count == 0 ? "作者未知" : string.Join("、", Authors);

    [JsonIgnore]
    public string StatusLine => $"{PageCount} 页 · {(HasCard ? "已分析" : "未分析")}" + (Warnings > 0 ? $" · {Warnings} warning" : "");
}

public sealed class PaperListResponse
{
    [JsonPropertyName("papers")]
    public List<PaperSummary> Papers { get; init; } = [];
}

public sealed class PaperDetailResponse
{
    [JsonPropertyName("paper")]
    public JsonElement Paper { get; init; }

    [JsonPropertyName("card")]
    public JsonElement? Card { get; init; }

    [JsonPropertyName("metadata_overrides")]
    public JsonElement MetadataOverrides { get; init; }

    [JsonPropertyName("references")]
    public List<JsonElement> References { get; init; } = [];

    [JsonPropertyName("citation_mentions")]
    public List<JsonElement> CitationMentions { get; init; } = [];
}

public sealed class AnalysisRequest
{
    [JsonPropertyName("research_context")]
    public string? ResearchContext { get; init; }

    [JsonPropertyName("exclude_after_text")]
    public string? ExcludeAfterText { get; init; }

    [JsonPropertyName("force")]
    public bool Force { get; init; }
}

public sealed class ModelSettingsView
{
    [JsonPropertyName("lite_api_key_masked")]
    public string LiteApiKeyMasked { get; init; } = "";

    [JsonPropertyName("has_lite_api_key")]
    public bool HasLiteApiKey { get; init; }

    [JsonPropertyName("lite_api_base_url")]
    public string LiteApiBaseUrl { get; init; } = "";

    [JsonPropertyName("lite_model")]
    public string LiteModel { get; init; } = "";

    [JsonPropertyName("pro_api_key_masked")]
    public string ProApiKeyMasked { get; init; } = "";

    [JsonPropertyName("has_pro_api_key")]
    public bool HasProApiKey { get; init; }

    [JsonPropertyName("pro_api_base_url")]
    public string ProApiBaseUrl { get; init; } = "";

    [JsonPropertyName("pro_model")]
    public string ProModel { get; init; } = "";

    [JsonPropertyName("same_connection")]
    public bool SameConnection { get; init; }
}

public sealed class ModelSettingsUpdate
{
    [JsonPropertyName("lite_api_key")]
    public string? LiteApiKey { get; init; }

    [JsonPropertyName("lite_api_base_url")]
    public string? LiteApiBaseUrl { get; init; }

    [JsonPropertyName("lite_model")]
    public string? LiteModel { get; init; }

    [JsonPropertyName("pro_api_key")]
    public string? ProApiKey { get; init; }

    [JsonPropertyName("pro_api_base_url")]
    public string? ProApiBaseUrl { get; init; }

    [JsonPropertyName("pro_model")]
    public string? ProModel { get; init; }

    [JsonPropertyName("pro_use_lite_connection")]
    public bool ProUseLiteConnection { get; init; }
}

public sealed class MetadataOverrideUpdate
{
    [JsonPropertyName("values")]
    public Dictionary<string, object?> Values { get; init; } = new();

    [JsonPropertyName("source_note")]
    public string? SourceNote { get; init; }
}

public sealed class DeletePaperResult
{
    [JsonPropertyName("paper_id")]
    public string PaperId { get; init; } = "";

    [JsonPropertyName("file_deleted")]
    public bool FileDeleted { get; init; }

    [JsonPropertyName("file_warning")]
    public string? FileWarning { get; init; }
}
