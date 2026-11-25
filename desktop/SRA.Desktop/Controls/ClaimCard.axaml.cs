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

public partial class ClaimCard : UserControl
{
    public static readonly RoutedEvent<EvidenceJumpEventArgs> EvidenceJumpRequestedEvent =
        RoutedEvent.Register<ClaimCard, EvidenceJumpEventArgs>(
            nameof(EvidenceJumpRequested),
            RoutingStrategies.Bubble);

    public event EventHandler<EvidenceJumpEventArgs>? EvidenceJumpRequested
    {
        add => AddHandler(EvidenceJumpRequestedEvent, value);
        remove => RemoveHandler(EvidenceJumpRequestedEvent, value);
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
}
