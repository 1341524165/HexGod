using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;

namespace hexgod
{
    sealed partial class Controller
    {
        bool overlayRequested, captureHidesOverlay;
        string lastDecisionSignature = "";
        [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr handle, int index);
        [DllImport("user32.dll")] static extern int SetWindowLong(IntPtr handle, int index, int value);

        void ToggleOverlay() {
            if (overlay != null) {
                overlayRequested = !overlayRequested;
                UpdateOverlayVisibility();
                if (overlayRequested) overlayTimer.Start(); else overlayTimer.Stop();
            }
            else CreateOverlay();
            Find<Button>("OverlayButton").Content = overlayRequested ? "隐藏悬浮窗" : "打开悬浮窗";
        }
        void UpdateOverlayVisibility() {
            if (overlay == null || closing) return;
            // Visibility is a user preference, never an API/mode/hero readiness gate.
            bool show = overlayRequested && !captureHidesOverlay;
            if (!show) { if (overlay.IsVisible) overlay.Hide(); WriteConnectionSnapshot(); return; }
            IntPtr handle = new WindowInteropHelper(overlay).Handle;
            if (handle != IntPtr.Zero) {
                int style = GetWindowLong(handle, -20);
                int next = style | 0x08000000 | 0x80; // NOACTIVATE, TOOLWINDOW.
                next = !Window.IsActive ? next | 0x20 : next & ~0x20; // Never intercept game clicks, including API outages.
                if (next != style) SetWindowLong(handle, -20, next);
            }
            if (!overlay.IsVisible) overlay.Show();
        }
        void EnsureOverlayOnTop() {
            UpdateOverlayVisibility();
            if (overlay == null || !overlay.IsVisible || closing) return;
            IntPtr handle = new WindowInteropHelper(overlay).Handle;
            if (handle != IntPtr.Zero && GameWindowProbe.IsForeground(liveGameHwnd)) {
                // A saved position on the assistant's monitor must not strand
                // the overlay outside a game on another monitor/window.
                var game = GameWindowProbe.ClientBounds(liveGameHwnd);
                WindowRect rect;
                if (game.Width >= 640 && game.Height >= 360 && GetWindowRect(handle, out rect) &&
                        !game.Contains(System.Drawing.Rectangle.FromLTRB(rect.Left, rect.Top, rect.Right, rect.Bottom))) {
                    int x = game.Right - (rect.Right - rect.Left) - 16;
                    int y = Math.Max(game.Top + 16, Math.Min(rect.Top, game.Bottom - (rect.Bottom - rect.Top) - 16));
                    SetWindowPos(handle, IntPtr.Zero, Math.Max(game.Left + 16, x), y, 0, 0, 0x0001 | 0x0004 | 0x0010);
                }
            } else if (handle != IntPtr.Zero) {
                // Switching from the narrow augment view to two item columns
                // must not leave half the wider overlay on another monitor.
                var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
                WindowRect rect;
                if (GetWindowRect(handle, out rect) && !work.Contains(System.Drawing.Rectangle.FromLTRB(rect.Left, rect.Top, rect.Right, rect.Bottom))) {
                    int x = Math.Max(work.Left + 8, Math.Min(rect.Left, work.Right - (rect.Right - rect.Left) - 8));
                    int y = Math.Max(work.Top + 8, Math.Min(rect.Top, work.Bottom - (rect.Bottom - rect.Top) - 8));
                    SetWindowPos(handle, IntPtr.Zero, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010);
                }
            }
            if (handle != IntPtr.Zero) SetWindowPos(handle, new IntPtr(-1), 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010 | 0x0200);
            WriteConnectionSnapshot();
        }
        void CreateOverlay() {
            overlayRequested = true;
            var physicalWork = System.Windows.Forms.Screen.FromHandle(new WindowInteropHelper(Window).Handle).WorkingArea;
            // PointFromScreen accounts for both monitor origin and DPI. A scale-only
            // conversion shifts the window off-screen on secondary monitors.
            var localTopLeft = Window.PointFromScreen(new Point(physicalWork.Left, physicalWork.Top));
            var localBottomRight = Window.PointFromScreen(new Point(physicalWork.Right, physicalWork.Bottom));
            var work = new Rect(
                new Point(Window.Left + localTopLeft.X, Window.Top + localTopLeft.Y),
                new Point(Window.Left + localBottomRight.X, Window.Top + localBottomRight.Y));
            overlay = new Window { Title = "hexgod · 实时建议", Width = 232, MaxHeight = work.Height - 24,
                SizeToContent = SizeToContent.Height, WindowStyle = WindowStyle.None, ResizeMode = ResizeMode.NoResize,
                AllowsTransparency = true, Background = Brushes.Transparent, Topmost = true, ShowInTaskbar = false,
                ShowActivated = false, FontFamily = new FontFamily("Microsoft YaHei UI") };
            overlay.SourceInitialized += (s, e) => {
                IntPtr handle = new WindowInteropHelper(overlay).Handle;
                SetWindowLong(handle, -20, GetWindowLong(handle, -20) | 0x08000000 | 0x80);
                var source = HwndSource.FromHwnd(handle);
                source.AddHook((IntPtr h, int message, IntPtr wp, IntPtr lp, ref bool handled) => {
                    if (message == 0x21) { handled = true; return new IntPtr(3); } // MA_NOACTIVATE.
                    return IntPtr.Zero;
                });
            };
            double desiredLeft = overlayLeft >= 0 ? overlayLeft : Window.Left + Window.ActualWidth - 248;
            double desiredTop = overlayTop >= 0 ? overlayTop : Window.Top + 70;
            overlay.Left = Math.Max(work.Left + 8, Math.Min(desiredLeft, work.Right - 240));
            overlay.Top = Math.Max(work.Top + 8, Math.Min(desiredTop, work.Bottom - 140));
            var root = new StackPanel();
            var header = new DockPanel { Margin = new Thickness(0, 0, 0, 7), Background = Brushes.Transparent, Cursor = Cursors.SizeAll };
            var close = new Button { Content = "×", Width = 20, Height = 20, Padding = new Thickness(0), Background = Brushes.Transparent, Foreground = BrushOf("#B4C4CE"), BorderThickness = new Thickness(0), Cursor = Cursors.Hand };
            DockPanel.SetDock(close, Dock.Right); header.Children.Add(close);
            overlayTitle = Text("HEXGOD", 11, "#9DDBC3", true);
            header.Children.Add(overlayTitle);
            close.Click += (s, e) => ToggleOverlay();
            header.MouseLeftButtonDown += (s, e) => { if (e.OriginalSource is TextBlock || e.OriginalSource == header) try { overlay.DragMove(); } catch { } };
            root.Children.Add(header); overlayContent = new StackPanel(); root.Children.Add(overlayContent);
            var scroll = new ScrollViewer { Content = root, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
            overlay.Content = new Border { Child = scroll, Padding = new Thickness(10), CornerRadius = new CornerRadius(9), Background = BrushOf("#B8101929"), BorderBrush = BrushOf("#6667898A"), BorderThickness = new Thickness(1) };
            overlay.Closing += (s, e) => { overlayLeft = overlay.Left; overlayTop = overlay.Top; };
            overlay.LocationChanged += (s, e) => { overlayLeft = overlay.Left; overlayTop = overlay.Top; };
            RenderOverlay(); overlay.Show(); EnsureOverlayOnTop(); overlayTimer.Start();
            try { SetWindowDisplayAffinity(new WindowInteropHelper(overlay).Handle, 0x11); } catch { }
        }
        static string ItemQuantity(Dictionary<string, object> item) {
            return Json.S(item, "name") + (Json.N(item, "quantity") > 1 ? " ×" + Json.N(item, "quantity") : "");
        }
        Border EquipmentSuggestion(Dictionary<string, object> item, bool mechanism) {
            var card = new StackPanel();
            var name = Text(Json.S(item, "name"), 12, mechanism ? "#C4FFDF" : "#E0EAF7", true);
            name.TextWrapping = TextWrapping.NoWrap; name.TextTrimming = TextTrimming.CharacterEllipsis;
            card.Children.Add(name);
            int games = (int)Json.N(item, "games");
            string rate = Json.Get(item, "winRate") == null ? "—" : Json.Percent(Json.N(item, "winRate"));
            string count = Json.Get(item, "games") == null ? "—" : games.ToString("N0");
            if (!mechanism) Add(card, rate + " · " + count + "场", 10, "#A4B9C9", false, 1);
            else card.VerticalAlignment = VerticalAlignment.Center;
            return new Border { Child = card, Padding = new Thickness(4), Margin = new Thickness(0, 4, 0, 0),
                MinHeight = 40,
                CornerRadius = new CornerRadius(5), Background = BrushOf(mechanism ? "#78305046" : "#401D2A39"),
                ToolTip = Json.S(item, "reason") + (mechanism ? "" : "\n按胜率与样本量综合排序；显示原始胜率。") };
        }
        void RenderEquipmentColumns() {
            var regularItems = Json.A(Json.Obj(buildPath), "regularItems").Select(Json.Obj).Take(3).ToArray();
            var synergyItems = Json.A(Json.Obj(buildPath), "synergyItems").Select(Json.Obj).Take(3).ToArray();
            bool hasSynergy = synergyItems.Length > 0;
            var columns = new Grid();
            columns.ColumnDefinitions.Add(new ColumnDefinition());
            if (hasSynergy) columns.ColumnDefinitions.Add(new ColumnDefinition());
            var regular = new StackPanel { Margin = new Thickness(0, 0, hasSynergy ? 4 : 0, 0) };
            Add(regular, "常规装备", 12, "#B6C8EA", true, 0);
            foreach (var row in regularItems) regular.Children.Add(EquipmentSuggestion(row, false));
            if (regularItems.Length == 0) Add(regular, "暂无候选", 11, "#A4B9C9", false, 4);
            columns.Children.Add(regular);
            if (hasSynergy) {
                var synergy = new StackPanel { Margin = new Thickness(4, 0, 0, 0) };
                Add(synergy, "海克斯联动", 12, "#9AD8BB", true, 0);
                foreach (var row in synergyItems) synergy.Children.Add(EquipmentSuggestion(row, true));
                Grid.SetColumn(synergy, 1); columns.Children.Add(synergy);
            }
            overlayContent.Children.Add(columns);
        }
        void RenderOverlay() {
            bool showChoices = choiceView.Visible || (overlayShowChoices && lastResult != null);
            var visibleChoices = choiceView.Visible ? choiceView.Result : lastResult;
            bool showStarter = Json.S(Json.Obj(buildPath), "recommendationStage") == "starter";
            string mode = showChoices ? (visibleChoices == null ? "augment_pending" : "augments") : showStarter ? "starter" : "items";
            string signature = Json.New().Serialize(Json.D("mode", mode, "hero", hero,
                "owned", ownedAugments.ToArray(), "inventory", liveItemIds.ToArray(),
                "next", Json.Get(Json.Obj(buildPath), "nextItems"),
                "regular", Json.Get(Json.Obj(buildPath), "regularItems"), "synergy", Json.Get(Json.Obj(buildPath), "synergyItems"),
                "choices", Json.Get(Json.Obj(visibleChoices), "choices")));
            if (signature != lastDecisionSignature) {
                lastDecisionSignature = signature;
                lastOverlayMode = mode;
                LogOverlayTransition(mode, visibleChoices);
            }
            if (overlayContent == null) return;
            overlayContent.Children.Clear();
            bool equipmentColumns = !showChoices && !showStarter && Json.Obj(buildPath).ContainsKey("regularItems");
            if (overlay != null) overlay.Width = equipmentColumns && Json.A(Json.Obj(buildPath), "synergyItems").Any() ? 320 : 232;
            var purchaseOrder = Json.Obj(Json.Get(Json.Obj(buildPath), "purchaseOrder"));
            string itemHeading = equipmentColumns ? "装备推荐" : Json.S(purchaseOrder, "basis") == "inventory_unknown" ? "装备方案 · 待同步" :
                Json.S(purchaseOrder, "basis") == "situational" ? "后续装备候选" : "下一件装备";
            if (overlayTitle != null) overlayTitle.Text = (showChoices ? "海克斯选择" : showStarter ? "出门装备" : itemHeading) +
                (hero.Length > 0 ? " · " + hero : "");
            if (showChoices) {
                if (visibleChoices == null) Add(overlayContent, "正在识别三组选项…", 12, "#D5E2EF", false, 3);
                foreach (object raw in Json.A(Json.Obj(visibleChoices), "choices")) {
                    var choice = Json.Obj(raw);
                    bool best = Json.B(choice, "recommended");
                    var line = new DockPanel();
                    var conditional = Json.Obj(Json.Get(choice, "conditional"));
                    string rate = conditional.Count > 0 ? Json.Percent(Json.N(conditional, "winRate")) :
                        Json.B(choice, "hasStats") ? Json.Percent(Json.N(choice, "winRate")) : "—";
                    var percent = Text(rate, 12, best ? "#9EF2CD" : "#B8C8D8", best);
                    DockPanel.SetDock(percent, Dock.Right); line.Children.Add(percent);
                    line.Children.Add(Text((best ? "➤ " : "  ") + Json.S(choice, "name"), 13,
                        best ? "#B5FFDC" : "#E2EAF3", best));
                    var choiceCard = new StackPanel();
                    choiceCard.Children.Add(line);
                    choiceCard.Children.Add(Text(Json.Get(choice, "games") == null ? "—场" : Json.N(choice, "games").ToString("N0") + "场", 10,
                        Json.N(choice, "games") < 1000 ? "#F3BC78" : "#A4B9C9", false));
                    overlayContent.Children.Add(new Border { Child = choiceCard, Padding = new Thickness(5, 6, 5, 6),
                        Margin = new Thickness(0, 0, 0, 3), CornerRadius = new CornerRadius(5),
                        Background = BrushOf(best ? "#78305046" : "#401D2A39"), ToolTip = Json.S(choice, "reason") });
                }
                if (selectionPending) Add(overlayContent, "等待选中确认…", 10, "#E7CA8D", false, 3);
            } else if (showStarter) {
                var plan = Json.Obj(Json.Get(Json.Obj(buildPath), "starterPlan"));
                foreach (var item in Json.A(plan, "items").Select(Json.Obj)) {
                    bool purchased = Json.N(item, "remainingCost") == 0;
                    bool supply = Json.B(item, "consumable");
                    var line = new DockPanel();
                    int quantity = (int)Json.N(item, "quantity");
                    string label = Json.S(item, "name") + (quantity > 1 ? " ×" +
                        (purchased ? quantity : quantity - (int)Json.N(item, "ownedQuantity")) : "");
                    line.Children.Add(Text((purchased ? "✓ " : "＋ ") + label, supply ? 11 : 13,
                        purchased ? "#91B3A4" : "#C4FFDF", !purchased && !supply));
                    overlayContent.Children.Add(new Border { Child = line, Padding = new Thickness(4, 6, 4, 6),
                        Margin = new Thickness(0, 0, 0, 3), CornerRadius = new CornerRadius(5),
                        Background = BrushOf(purchased ? "#301D2A39" : "#78305046") });
                }
            } else if (equipmentColumns) {
                RenderEquipmentColumns();
            } else {
                var order = Json.Obj(Json.Get(Json.Obj(buildPath), "purchaseOrder"));
                string orderBasis = Json.S(order, "basis");
                if (orderBasis == "curated") Add(overlayContent, "核心顺序参考 · " +
                    String.Join(" → ", Json.A(order, "routeNames").Select(Convert.ToString)), 10, "#E7CA8D", false, 0);
                else if (orderBasis == "common_first_fallback") Add(overlayContent, "首件参考 · 从常用装备中比较", 10, "#B8C8D8", false, 0);
                else if (orderBasis == "formula_reference") Add(overlayContent, "本地核心顺序 · 启发式方案", 10, "#E7CA8D", false, 0);
                else if (orderBasis == "components") Add(overlayContent, "优先合成已购小件", 10, "#E7CA8D", false, 0);
                else if (orderBasis == "aramgg_core") Add(overlayContent, "ARAMGG 核心组合参考", 10, "#E7CA8D", false, 0);
                else if (orderBasis == "mechanism_rule") Add(overlayContent, "按已选海克斯调整 · 机制规则", 10, "#E7CA8D", false, 0);
                var next = Json.A(Json.Obj(buildPath), "nextItems").Select(Json.Obj).ToArray();
                if (next.Length == 0) Add(overlayContent, hero.Length == 0 ? "选择英雄后显示出装" : "暂无可用的下一件建议", 12, "#D5E2EF", false, 3);
                for (int i = 0; i < next.Length; i++) {
                    var item = next[i];
                    bool first = i == 0;
                    var card = new StackPanel();
                    var line = new DockPanel();
                    int games = (int)Json.N(item, "games");
                    string rate = Json.Get(item, "winRate") != null && games > 0
                        ? Json.Percent(Json.N(item, "winRate")) : "—";
                    var percent = Text(rate, 11, first ? "#A7F0CB" : "#A9BCCD", first);
                    DockPanel.SetDock(percent, Dock.Right); line.Children.Add(percent);
                    line.Children.Add(Text((first ? "→ " : "   ") + Json.S(item, "name"), first ? 14 : 12,
                        first ? "#C4FFDF" : "#D5E2EF", first));
                    card.Children.Add(line);
                    Add(card, Json.B(item, "mechanismOnly") || Json.S(item, "evidenceBasis") == "mechanism_route" ?
                        "机制推荐 · 无装备联合胜率" : games > 0 ? Json.Count(games) + " 场" +
                        (Json.B(item, "sampleLimited") ? " · 小样本" : "") : "暂无样本", 10,
                        Json.B(item, "sampleLimited") ? "#E6BB88" : "#A3B6C8", false, 2);
                    if (Json.B(item, "mechanismOnly")) Add(card, Json.S(item, "reason"), 10, "#96AABD", false, 2);
                    if (liveItemsKnown && Json.Get(item, "remainingCost") != null) Add(card,
                        "合成还需 " + Json.N(item, "remainingCost") + " 金" + (first ? "" : " · 备选"), 10, "#96AABD", false, 2);
                    overlayContent.Children.Add(new Border { Child = card, Padding = new Thickness(4, 5, 4, 5),
                        Margin = new Thickness(0, 0, 0, 2), CornerRadius = new CornerRadius(5),
                        Background = BrushOf(first ? "#78305046" : "#401D2A39") });
                }
                var nextIds = next.Select(i => Json.N(i, "id")).ToArray();
                var tips = Json.A(Json.Obj(buildPath), "synergyItems").Select(Json.Obj)
                    .Where(i => !nextIds.Contains(Json.N(i, "id"))).Take(2).ToArray();
                if (tips.Length > 0) {
                    Add(overlayContent, "联动备选 · 机制参考", 10, "#9AD8BB", true, 9);
                    foreach (var item in tips) {
                        int games = (int)Json.N(item, "games");
                        bool limited = Json.B(item, "sampleLimited");
                        string note = Json.B(item, "mechanismOnly") ? "机制 · 无联合样本" : games > 0 ? Json.Count(games) + "场" : "无样本";
                        if (Json.B(item, "laterOnly")) note += " · 被动未达";
                        else if (limited) note += " · 小样本";
                        var line = new DockPanel { Margin = new Thickness(0, 4, 0, 0) };
                        string rate = Json.Get(item, "winRate") != null && games > 0
                            ? Json.Percent(Json.N(item, "winRate")) : "—";
                        var stats = Text(rate + " · " + note, 10, limited ? "#DFB78F" : "#AFC9BD", false);
                        stats.TextWrapping = TextWrapping.NoWrap;
                        DockPanel.SetDock(stats, Dock.Right); line.Children.Add(stats);
                        var name = Text(Json.S(item, "name"), 11, "#C0D4C9", false);
                        name.TextWrapping = TextWrapping.NoWrap;
                        name.TextTrimming = TextTrimming.CharacterEllipsis;
                        name.Margin = new Thickness(0, 0, 4, 0);
                        line.Children.Add(name);
                        overlayContent.Children.Add(line);
                    }
                }
                if (next.Length > 0) Add(overlayContent,
                    liveItemsKnown ? "装备已同步 · " +
                        (orderBasis == "mechanism_rule" ? "已选机制路线" : Json.S(Json.Obj(buildPath), "dataBasis") == "joint" ? "海克斯联合样本" : "来源装备组合参考")
                        : "装备未同步 · 尚未排除已出", 10, "#A4B9C9", false, 5);
                var preview = Json.A(Json.Obj(buildPath), "planPreview").Select(Json.Obj).Take(3).ToArray();
                if (preview.Length > 1 && (orderBasis == "formula_reference" || orderBasis == "curated")) Add(overlayContent,
                    "方案：" + String.Join(" → ", preview.Select(i => Json.S(i, "name"))) + "\n非整套路径胜率", 10, "#96AABD", false, 5);
                var diagnostics = Json.A(Json.Obj(Json.Get(Json.Obj(buildPath), "buildProfile")), "diagnostics").Select(Convert.ToString).Take(1).ToArray();
                if (diagnostics.Length > 0) Add(overlayContent, diagnostics[0], 10, "#E7CA8D", false, 5);
            }
            if (overlayTitle != null) overlayTitle.ToolTip = "自动记录 " + ownedAugments.Count + " 项：" + String.Join("、", ownedAugments) +
                "\n" + Json.S(Json.Obj(buildPath), "sourceNote") + (liveItemsKnown ? "\n装备已同步" : "\n装备尚未同步");
        }
        void LogOverlayTransition(string mode, Dictionary<string, object> choices) {
            if (!ready) return; // Hidden renderer tests are not real match history.
            try {
                string path = ProjectPaths.LogFile(appDir, "overlay-events.log");
                if (File.Exists(path) && new FileInfo(path).Length > 1024 * 1024)
                    File.WriteAllText(path, "", Encoding.UTF8);
                File.AppendAllText(path, Json.New().Serialize(Json.D("at", DateTime.Now.ToString("s"),
                    "session", session.Generation, "phase", session.Phase,
                    "inventory", liveItemIds.ToArray(), "itemsKnown", liveItemsKnown,
                    "choices", Json.A(Json.Obj(choices), "choices").Select(Json.Obj).Select(c => Json.S(c, "name")).ToArray(),
                    "hero", hero, "mode", mode, "gameTime", Json.Get(Json.Obj(liveContext), "gameTime"),
                    "owned", ownedAugments.ToArray(), "matched", Json.N(Json.Obj(choices), "matchedCount"),
                    "openingReason", Json.S(Json.Obj(buildPath), "openingReason"),
                    "captureReason", Json.S(Json.Obj(lastFrame), "captureReason"),
                    "titleProbe", Json.B(Json.Obj(lastFrame), "titleProbe"),
                    "nextItems", Json.A(Json.Obj(buildPath), "nextItems").Select(Json.Obj)
                        .Select(item => Json.Get(item, "id")).ToArray(),
                    "regularItems", Json.A(Json.Obj(buildPath), "regularItems").Select(Json.Obj).Select(item => Json.Get(item, "id")).ToArray(),
                    "regularSort", Json.S(Json.Obj(buildPath), "regularSort"),
                    "regularEvidence", Json.A(Json.Obj(buildPath), "regularItems").Select(Json.Obj).Select(item =>
                        Json.D("id", Json.Get(item, "id"), "name", Json.S(item, "name"), "winRate", Json.Get(item, "winRate"),
                            "games", Json.Get(item, "games"), "sortScore", Json.Get(item, "sortScore"))).ToArray(),
                    "synergyItems", Json.A(Json.Obj(buildPath), "synergyItems").Select(Json.Obj).Take(3).Select(item => Json.Get(item, "id")).ToArray(),
                    "mechanismFeatures", Json.Get(Json.Obj(Json.Get(Json.Obj(buildPath), "buildProfile")), "features"))) + Environment.NewLine, Encoding.UTF8);
            } catch { }
        }
    }

}
