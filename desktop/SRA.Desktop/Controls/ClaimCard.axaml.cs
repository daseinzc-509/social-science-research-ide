using Avalonia.Controls;
using Avalonia.Interactivity;
using SRA.Desktop.Models;

namespace SRA.Desktop.Controls;

/// <summary>
/// 点击 ClaimCard 原文证据条目时向上冒泡的定位事件参数。
/// </summary>
public sealed class EvidenceJumpEventArgs(RoutedEvent routedEvent, EvidenceItem evidence)
    : RoutedEventArgs(routedEvent)
{
    public EvidenceItem Evidence { get; } = evidence;
}

/// <summary>
/// 点击 ClaimCard 底部就地人工审定按钮时向上冒泡的事件参数。
/// </summary>
public sealed class ClaimReviewRequestedEventArgs(RoutedEvent routedEvent, ClaimItem claim, string action)
    : RoutedEventArgs(routedEvent)
{
    public ClaimItem Claim { get; } = claim;
    public string Action { get; } = action;
}

public partial class ClaimCard : UserControl
{
    public static readonly RoutedEvent<EvidenceJumpEventArgs> EvidenceJumpRequestedEvent =
        RoutedEvent.Register<ClaimCard, EvidenceJumpEventArgs>(
            nameof(EvidenceJumpRequested),
            RoutingStrategies.Bubble);

    public static readonly RoutedEvent<ClaimReviewRequestedEventArgs> ClaimReviewRequestedEvent =
        RoutedEvent.Register<ClaimCard, ClaimReviewRequestedEventArgs>(
            nameof(ClaimReviewRequested),
            RoutingStrategies.Bubble);

    public event EventHandler<EvidenceJumpEventArgs>? EvidenceJumpRequested
    {
        add => AddHandler(EvidenceJumpRequestedEvent, value);
        remove => RemoveHandler(EvidenceJumpRequestedEvent, value);
    }

    public event EventHandler<ClaimReviewRequestedEventArgs>? ClaimReviewRequested
    {
        add => AddHandler(ClaimReviewRequestedEvent, value);
        remove => RemoveHandler(ClaimReviewRequestedEvent, value);
    }

    public ClaimCard()
    {
        InitializeComponent();
    }

    private void EvidenceJump_Click(object? sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: EvidenceItem item })
        {
            RaiseEvent(new EvidenceJumpEventArgs(EvidenceJumpRequestedEvent, item));
        }
    }

    private void ReviewAction_Click(object? sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: string action } && DataContext is ClaimItem claim)
        {
            RaiseEvent(new ClaimReviewRequestedEventArgs(ClaimReviewRequestedEvent, claim, action));
        }
    }
}
