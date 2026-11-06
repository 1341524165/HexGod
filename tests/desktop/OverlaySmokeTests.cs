using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using hexgod;

// Exercise the real C# renderer and Python protocol without showing a window
// or registering hotkeys in the player's desktop session.
static class OverlaySmokeTests {
    static readonly BindingFlags Fields = BindingFlags.Instance | BindingFlags.NonPublic;
    static void Set(Controller c, string field, object value) { typeof(Controller).GetField(field, Fields).SetValue(c, value); }
    static void Assert(bool condition, string message) { if (!condition) throw new Exception(message); }
    static void Render(Controller c) { typeof(Controller).GetMethod("RenderOverlay", Fields).Invoke(c, null); }
    static Dictionary<string, object> Frame(long captured, bool panel, int matched = 3, string prefix = "原", bool skipped = false, long hwnd = 41) {
        return Json.D("capturedAtMs", captured, "augmentPanelVisible", panel, "matchedCount", matched,
            "captureSkipped", skipped, "gameWindowHwnd", hwnd, "choices", Enumerable.Range(0, 3)
            .Select(i => Json.D("name", prefix + i, "matched", i < matched)).ToArray());
    }
    static void CheckTransitions() {
        var view = new AugmentViewState();
        view.Observe(Frame(1000, true, 0), 1000);
        Assert(view.Visible && view.Result == null, "Detected opening panel must override starter while OCR is pending");
        view.Observe(Frame(1500, true), 1500);
        Assert(view.Visible && view.Result != null, "Opening titles did not appear");
        view.Observe(Frame(2000, false, 0), 2000);
        Assert(view.Visible, "One failed frame switched back to items");
        view.Observe(Frame(2500, true), 2500);
        Assert(view.Visible, "Recovered OCR did not remain visible");
        view.Selected(2700);
        Assert(!view.Visible, "Click did not switch immediately");
        view.Observe(Frame(2600, true), 2800);
        Assert(!view.Visible, "In-flight frame from before click reopened choices");
        view.Observe(Frame(3000, true), 3000);
        Assert(!view.Visible, "Closing animation reopened selected choices");
        view.Observe(Frame(3500, false, 0), 3500);
        view.Observe(Frame(4000, false, 0), 4000);
        view.Observe(Frame(4500, true), 4500);
        Assert(view.Visible, "Subsequent selection round remained blocked");
        view.Observe(Frame(5000, true, 0), 5000);
        Assert(view.Result != null, "Brief title dropout lost readable results");
        view.Observe(Frame(6000, true, 0), 6000);
        Assert(view.Visible && view.Result == null, "Unreadable panel must show pending, not stale titles or starter");
        view.Observe(Frame(6500, false, 0, "原", true), 6500);
        Assert(view.Visible, "Alt-tab was treated as panel closure");
        view.Observe(Frame(9000, false, 0, "原", true), 9000);
        Assert(!view.Visible, "Skipped capture kept stale choices indefinitely");
        view.Reset();
        view.Observe(Frame(10000, true), 10000);
        view.Selected(10500);
        view.Observe(Frame(11000, true, 3, "新"), 11000);
        Assert(view.Visible, "New/rerolled options should display immediately");
        view.Observe(Frame(11500, false, 0, "原", false, 42), 11500);
        Assert(!view.Visible, "New game process retained old choices");
        view.Reset();
        Assert(!view.Visible && view.SelectionAt == 0, "New match did not reset selection cutoff");
    }
    static void CheckSessions() {
        var session = new GameSessionState();
        Func<string, string, Dictionary<string, object>> phase = (p, id) => Json.D("available", true,
            "hero", "暗夜猎手", "gameTime", 100, "lcu", Json.D("available", true, "phase", p, "gameId", id, "gameMode", "ARAM"));
        var selecting = phase("ChampSelect", "1"); selecting["available"] = false;
        Assert(!session.Update(selecting, 1000) && !session.CanCapture, "ChampSelect enabled capture");
        Assert(session.Update(phase("InProgress", "1"), 2000) && session.CanCapture, "Game start not observed");
        Assert(!session.Update(Json.D("available", false, "gameWindowPresent", true), 3000) && session.Active, "API outage reset active match");
        Assert(session.Update(phase("EndOfGame", "1"), 4000) && !session.Active, "Game end did not clear even with stale Live API");
        Assert(session.Update(phase("InProgress", "2"), 5000), "Same hero next game did not reset");
        var rollback = phase("InProgress", "2"); rollback["gameTime"] = 5;
        Assert(session.Update(rollback, 6000), "Clock rollback did not delimit new fallback game");
        var unsupported = phase("InProgress", "3"); Json.Obj(unsupported["lcu"])["gameMode"] = "CLASSIC";
        session.Update(unsupported, 7000);
        Assert(!session.CanCapture, "Unsupported mode captured");
        session.Update(Json.D("available", false, "gameWindowPresent", true), 8000);
        Assert(!session.CanCapture, "Missing mode forgot known unsupported mode");
        Assert(!session.Update(Json.D("available", false), 9000), "One missing process poll ended match");
        Assert(session.Update(Json.D("available", false), 19000) && !session.Active, "Vanished process never cleared match");
        var mayhem = phase("InProgress", "4"); Json.Obj(mayhem["lcu"])["gameMode"] = "KIWI";
        Assert(session.Update(mayhem, 20000) && session.CanCapture, "KIWI/Mayhem was rejected as unsupported");
        var staleLobby = phase("Lobby", "4"); staleLobby["gameWindowPresent"] = true;
        staleLobby["gameMode"] = "KIWI";
        Assert(!session.Update(staleLobby, 21000) && session.CanCapture, "Stale lobby state hid running game");
        var unknown = Json.D("available", false, "gameWindowPresent", true);
        Assert(!session.Update(unknown, 22000) && session.CanCapture, "LCU outage disabled known Mayhem game");
    }
    static void CheckBufferedClick(Controller c) {
        var buffer = typeof(Controller).GetMethod("BufferGameClick", Fields);
        var correlate = typeof(Controller).GetMethod("CorrelateBufferedClick", Fields);
        Set(c, "trackingGeometry", null);
        buffer.Invoke(c, new object[] { 600, 500, (long)41, (long)1500 });
        Assert(typeof(Controller).GetField("queuedClick", Fields).GetValue(c) == null, "Click guessed an unrecognized title");
        correlate.Invoke(c, new object[] { Json.D("windowHwnd", 41, "capturedAtMs", 1400) });
        Assert(typeof(Controller).GetField("queuedClick", Fields).GetValue(c) != null, "Fast selection before OCR response was dropped");
        Set(c, "queuedClick", null); Set(c, "bufferedClick", null);
        buffer.Invoke(c, new object[] { 600, 500, (long)41, (long)1500 });
        correlate.Invoke(c, new object[] { Json.D("windowHwnd", 41, "capturedAtMs", 1600) });
        Assert(typeof(Controller).GetField("queuedClick", Fields).GetValue(c) == null, "Post-click frame invented a choice");
        var geometry = Json.D("windowHwnd", 41, "capturedAtMs", 2000,
            "names", new object[] { "old", "middle", "right" },
            "hitboxes", new[] { new[] { 10, 10, 100, 100 }, new[] { 110, 10, 200, 100 }, new[] { 210, 10, 300, 100 } },
            "rerollHitboxes", new[] { new[] { 20, 110, 90, 140 }, new[] { 120, 110, 190, 140 }, new[] { 220, 110, 290, 140 } });
        Set(c, "trackingGeometry", geometry); Set(c, "bufferedClick", null);
        buffer.Invoke(c, new object[] { 50, 125, (long)41, (long)2200 });
        Assert(Json.A(geometry, "names").First() == null, "Reroll kept stale slot title available for selection");
        Assert(typeof(Controller).GetField("queuedClick", Fields).GetValue(c) == null, "Reroll was queued as selection");
        buffer.Invoke(c, new object[] { 50, 50, (long)41, (long)2300 });
        var queued = Json.Obj(typeof(Controller).GetField("queuedClick", Fields).GetValue(c));
        Assert(Json.A(Json.Obj(queued["geometry"]), "names").First() == null, "Fast click after reroll guessed the old title");
        typeof(Controller).GetMethod("ResetMatchContext", Fields).Invoke(c, null);
        Assert(typeof(Controller).GetField("bufferedClick", Fields).GetValue(c) == null, "Context reset retained old click");
    }
    static void CheckRawInput(Controller c) {
        var listener = (GameClickListener)typeof(Controller).GetField("clickListener", Fields).GetValue(c);
        Assert(listener.Installed, "Real background Raw Input registration failed: " + listener.Error);
        Assert(listener.Method == "raw_input+button_state" && listener.Polling, "Independent button-state source missing");
        var state = new MousePressTracker();
        Assert(!state.ObserveState(true), "Starting while held invented a click");
        Assert(!state.ObserveState(true) && !state.ObserveState(false), "Holding/releasing became a click");
        Assert(state.ObserveState(true) && !state.ObserveState(true), "Physical press was lost or repeated");
        Assert(state.Accept("button_state", 1000), "Fallback did not accept an event without Raw Input");
        Assert(!state.Accept("raw_input", 1010), "Resumed Raw Input duplicated the fallback click");
        Assert(state.Accept("raw_input", 1040), "A fast second physical press was dropped");
        Assert(!state.ObserveState(false) && state.ObserveState(true), "Fallback did not reset after release");
        Assert(state.Accept("button_state", 1200), "Fallback did not recover after another Raw Input stall");
        IntPtr packet = Marshal.AllocHGlobal(64);
        try {
            Marshal.Copy(new byte[64], 0, packet, 64);
            int headerSize = 8 + 2 * IntPtr.Size;
            Assert(!GameClickListener.IsLeftButtonDown(packet, 48), "Mouse movement became a selection");
            Marshal.WriteInt16(packet, headerSize + 4, 1);
            Assert(GameClickListener.IsLeftButtonDown(packet, (uint)(headerSize + 24)), "Physical left-button transition missed");
            Assert(!GameClickListener.IsLeftButtonDown(packet, (uint)(headerSize + 23)), "Truncated packet accepted");
            Marshal.WriteInt32(packet, 0, 1);
            Assert(!GameClickListener.IsLeftButtonDown(packet, 48), "Keyboard packet became a selection");
            Marshal.WriteInt32(packet, 0, 0);
            foreach (short flags in new short[] { 2, 4, 8, 0x400 }) {
                Marshal.WriteInt16(packet, headerSize + 4, flags);
                Assert(!GameClickListener.IsLeftButtonDown(packet, 48), "Release/right/wheel packet became a selection");
            }
        } finally { Marshal.FreeHGlobal(packet); }
    }
    static string Contents(DependencyObject node) {
        string result = node is TextBlock ? ((TextBlock)node).Text + "\n" : "";
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++) result += Contents(VisualTreeHelper.GetChild(node, i));
        return result;
    }
    static void CheckEquipmentColumns(StackPanel content, int expectedSynergyRows) {
        var columns = content.Children[0] as Grid;
        Assert(columns != null && columns.ColumnDefinitions.Count == (expectedSynergyRows > 0 ? 2 : 1), "Empty synergy column kept its space");
        if (expectedSynergyRows == 0) Assert(!Contents(content).Contains("海克斯联动"), "Empty synergy header remains");
        else {
            var synergy = columns.Children[1] as StackPanel;
            Assert(synergy != null && synergy.Children.Count == expectedSynergyRows + 1, "Synergy suggestions padded to a fixed count");
        }
    }
    static void Save(StackPanel content, TextBlock title, string path, int maximumHeight = 230, int width = 232) {
        var stack = new StackPanel(); stack.Children.Add(title); stack.Children.Add(content);
        var frame = new Border { Child = stack, Padding = new Thickness(10), Width = width,
            Background = new SolidColorBrush(Color.FromRgb(16, 25, 41)),
            CornerRadius = new CornerRadius(9) };
        frame.Measure(new Size(width, 1000)); frame.Arrange(new Rect(frame.DesiredSize)); frame.UpdateLayout();
        Assert(frame.ActualHeight < maximumHeight, "Overlay unexpectedly tall");
        var bitmap = new RenderTargetBitmap(width, (int)Math.Ceiling(frame.ActualHeight), 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(frame);
        var png = new PngBitmapEncoder(); png.Frames.Add(BitmapFrame.Create(bitmap));
        using (var file = File.Create(path)) png.Save(file);
        stack.Children.Clear();
    }
    [STAThread]
    public static int Main(string[] args) {
        Controller c = null;
        BackendClient backend = null;
        BackendClient liveBackend = null;
        try {
            if (args.Length > 0 && args[0] == "--input-probe") {
                using (var listener = new GameClickListener()) {
                    Assert(listener.Installed, "Raw Input registration failed: " + listener.Error);
                    for (int i = 0; i < 20; i++) {
                        System.Threading.Thread.Sleep(1000);
                        Console.WriteLine("raw_input: installed={0}, packets={1}, clicks={2}, error={3}",
                            listener.Installed, listener.Packets, listener.ObservedClicks, listener.Error);
                    }
                }
                return 0;
            }
            CheckTransitions();
            CheckSessions();
            // Exercise the inherited launcher environment as shipped.
            string root = Path.GetFullPath(args[0]);
            Assert(ProjectPaths.FindRoot(Path.Combine(root, "build", "bin")) == root,
                "Desktop build output cannot locate the repository root");
            c = new Controller(root);
            var card = (Border)typeof(Controller).GetMethod("EquipmentSuggestion", Fields).Invoke(c,
                new object[] { Json.D("name", "纳什之牙", "triggerAugments", new[] { "虚幻武器", "其他强化" }), true });
            var cardContent = (StackPanel)card.Child;
            Assert(cardContent.Children.Count == 2 && ((TextBlock)cardContent.Children[1]).Text == "虚幻武器 / 其他强化",
                "Synergy card did not show its augment sources");
            CheckRawInput(c);
            CheckBufferedClick(c);
            backend = (BackendClient)typeof(Controller).GetField("backend", Fields).GetValue(c);
            liveBackend = (BackendClient)typeof(Controller).GetField("liveBackend", Fields).GetValue(c);
            Func<Dictionary<string, object>, Dictionary<string, object>> request = input =>
                Task.Run(() => backend.Request(input)).GetAwaiter().GetResult();
            var content = new StackPanel();
            var title = new TextBlock { Foreground = Brushes.LightGreen, FontSize = 11, Margin = new Thickness(0, 0, 0, 7) };
            Set(c, "overlayContent", content); Set(c, "overlayTitle", title);
            if (args.Length > 1 && args[1] == "--production") {
                var overlayWindow = new Window { Width = 232 }; Set(c, "overlay", overlayWindow);
                var catalog = request(Json.D("action", "catalog"));
                Assert(Json.S(Json.Obj(catalog["meta"]), "source").Contains("ARAMGG"), "Production source is not ARAMGG");
                Set(c, "hero", "复仇焰魂"); Set(c, "liveItemsKnown", true);
                Set(c, "buildPath", request(Json.D("action", "build_path", "hero", "复仇焰魂",
                    "itemIds", new[] { 2503, 3020 }, "itemsKnown", true)));
                Render(c);
                var currentBuild = Json.Obj(typeof(Controller).GetField("buildPath", Fields).GetValue(c));
                var currentRegular = Json.A(currentBuild, "regularItems").Select(Json.Obj).Take(3).ToArray();
                Assert(currentRegular.Length == 3 && currentRegular.All(row => Contents(content).Contains(Json.S(row, "name"))), "Three current sample-adjusted choices missing");
                Assert(!Contents(content).Contains("黯炎火炬") && !Contents(content).Contains("法师之靴"), "Owned equipment remained in visible suggestions");
                Assert(!Contents(content).Contains("合成还需") && !Contents(content).Contains("机制适配") && !Contents(content).Contains("单件统计"), "Verbose equipment text remains");
                Assert(!Contents(content).Contains("海克斯联动") && title.Text.StartsWith("装备推荐"), "Empty synergy column remains");
                CheckEquipmentColumns(content, 0); Assert(overlayWindow.Width == 232, "Overlay did not shrink without synergy");
                Save(content, title, Path.Combine(root, "artifacts", "cache", "aramgg-items.png"), 195, (int)overlayWindow.Width);
                var recorded = (List<string>)typeof(Controller).GetField("ownedAugments", Fields).GetValue(c);
                recorded.Add("虚幻武器");
                Set(c, "buildPath", request(Json.D("action", "build_path", "hero", "复仇焰魂", "owned", new[] { "虚幻武器" }, "itemIds", new int[0], "itemsKnown", true)));
                Render(c);
                Assert(Contents(content).Contains("纳什之牙") && Contents(content).Contains("巫妖之祸") && !Contents(content).Contains("—场"), "Spell-on-hit names missing or unnecessary stats placeholder remains");
                Assert(!Contents(content).Contains("技能施加") && !Contents(content).Contains("无联合胜率"), "Repeated mechanism explanation remains");
                CheckEquipmentColumns(content, 3); Assert(overlayWindow.Width == 320, "Overlay failed to expand for synergy");
                Save(content, title, Path.Combine(root, "artifacts", "cache", "mechanics-brand.png"), 195, 320);
                Set(c, "hero", "齐天大圣");
                recorded.Clear(); recorded.Add("升级：无尽之刃");
                Set(c, "buildPath", request(Json.D("action", "build_path", "hero", "齐天大圣", "owned", new[] { "升级：无尽之刃" }, "itemIds", new int[0], "itemsKnown", true)));
                Render(c);
                currentBuild = Json.Obj(typeof(Controller).GetField("buildPath", Fields).GetValue(c));
                currentRegular = Json.A(currentBuild, "regularItems").Select(Json.Obj).Take(3).ToArray();
                Assert(Contents(content).Contains("无尽之刃") && currentRegular.All(row =>
                    Contents(content).Contains(Json.S(row, "name")) && (Json.Get(row, "games") == null ||
                    Contents(content).Contains(Json.N(row, "games").ToString("N0") + "场"))), "Upgrade or current sample sizes missing");
                Assert(!Contents(content).Contains("海克斯镜片") && !Contents(content).Contains("育恩塔尔"), "Infinity upgrade added unrelated crit equipment");
                CheckEquipmentColumns(content, 1);
                Save(content, title, Path.Combine(root, "artifacts", "cache", "mechanics-infinity.png"), 195, 320);
                Set(c, "buildPath", request(Json.D("action", "build_path", "hero", "齐天大圣", "owned", new[] { "升级：无尽之刃", "升级：死亡之舞" }, "itemIds", new int[0], "itemsKnown", true)));
                Render(c); CheckEquipmentColumns(content, 2);
                Set(c, "buildPath", request(Json.D("action", "build_path", "hero", "齐天大圣", "owned", new[] { "升级：无尽之刃" }, "itemIds", new[] {3031}, "itemsKnown", true)));
                Render(c); CheckEquipmentColumns(content, 0);
                Assert(overlayWindow.Width == 232, "Completed upgrade kept the wide overlay");
                Save(content, title, Path.Combine(root, "artifacts", "cache", "mechanics-infinity-bought.png"), 195, (int)overlayWindow.Width);
                Set(c, "buildPath", request(Json.D("action", "build_path", "hero", "齐天大圣", "owned", new[] { "捐赠" }, "itemIds", new int[0], "itemsKnown", true)));
                Render(c); CheckEquipmentColumns(content, 0);
                Assert(overlayWindow.Width == 232, "Augment without equipment direction expanded the overlay");
                Set(c, "hero", "德邦总管");
                Set(c, "buildPath", request(Json.D("action", "build_path", "hero", "德邦总管", "owned", new[] { "虚幻武器" }, "itemIds", new int[0], "itemsKnown", true)));
                Render(c); CheckEquipmentColumns(content, 3);
                Assert(overlayWindow.Width == 320 && Contents(content).Contains("破败王者之刃"), "Xin Zhao spell-on-hit did not expand visible synergy column");
                Save(content, title, Path.Combine(root, "artifacts", "cache", "xinzhao-spell-on-hit.png"), 195, 320);
                Set(c, "hero", "复仇焰魂");
                recorded.Clear();
                Set(c, "lastResult", request(Json.D("action", "rank", "hero", "复仇焰魂",
                    "choices", new[] { "虚幻武器", "炼狱导管", "战争交响乐" })));
                Set(c, "overlayShowChoices", true); Render(c);
                Assert(title.Text.StartsWith("海克斯选择") && !Contents(content).Contains("ARAMGG") && !Contents(content).Contains("单项胜率"), "Augment overlay still has verbose metadata");
                Save(content, title, Path.Combine(root, "artifacts", "cache", "compact-augments.png"), 195);
                overlayWindow.Close();
                Console.WriteLine("PASS: ARAMGG production protocol, inventory order, evidence patches and overlay rendering.");
                return 0;
            }
            int start = 100000;
            foreach (string hero in new[] { "镕铁少女", "暗夜猎手" }) {
                Set(c, "hero", hero);
                var live = Json.D("available", true, "hero", hero, "gameTime", 12, "currentGold", 1400,
                    "deaths", 0, "matchStartMs", start);
                var path = request(Json.D("action", "build_path", "hero", hero, "itemIds", new int[0],
                    "itemsKnown", true, "liveContext", live));
                Set(c, "buildPath", path); Set(c, "liveItemsKnown", true); Render(c);
                Assert(title.Text.StartsWith("出门装备"), "Starter title missing");
                string text = Contents(content);
                Assert(text.Contains(hero == "镕铁少女" ? "女神之泪" : "守护者之刃"), "Starter item missing");
                Assert(!text.Contains(" 金") && !text.Contains("无组合样本"), "Starter overlay still contains verbose budget/evidence text");
                if (hero == "暗夜猎手") Assert(text.Contains("生命药水 ×2"), "Potion quantity missing");
                Save(content, title, Path.Combine(root, "artifacts", "cache", hero == "镕铁少女" ? "starter-rell.png" : "starter-vayne.png"));

                Set(c, "lastResult", request(Json.D("action", "demo", "hero", hero)));
                Set(c, "overlayShowChoices", true); Render(c);
                Assert(title.Text.StartsWith("海克斯选择"), "Augment choices lost priority");
                Set(c, "overlayShowChoices", false); Render(c);
                Assert(title.Text.StartsWith("出门装备"), "Starter did not return after choices");

                var view = (AugmentViewState)typeof(Controller).GetField("choiceView", Fields).GetValue(c);
                view.Observe(Frame(1000, true, 0), 1000); Render(c);
                Assert(title.Text.StartsWith("海克斯选择") && Contents(content).Contains("正在识别"),
                    "Opening starter covered a detected but unreadable panel");
                // Inventory refresh while a panel is visible must not change its view.
                Set(c, "buildPath", path); Render(c);
                Assert(title.Text.StartsWith("海克斯选择"), "Inventory refresh stole augment priority");
                view.Observe(Frame(1500, false, 0), 1500); Render(c);
                Assert(title.Text.StartsWith("海克斯选择"), "One blank scan showed starter");
                view.Observe(Frame(2000, false, 0), 2000); Render(c);
                Assert(title.Text.StartsWith("出门装备"), "Confirmed disappearance failed to restore starter");
                view.Reset();

                live["gameTime"] = 91;
                Set(c, "buildPath", request(Json.D("action", "build_path", "hero", hero, "itemIds", new int[0],
                    "itemsKnown", true, "liveContext", live)));
                Render(c);
                Assert(title.Text.StartsWith("下一件装备"), "Opening deadline did not switch renderer");
                Assert(!Contents(content).Contains("整套 1400"), "Stale starter text remains");
                start += 900000;
            }
            // Live API requests and OCR use independent protocol clients.
            Assert(!Object.ReferenceEquals(backend, liveBackend), "Live polling shares the OCR request queue");
            Set(c, "hero", "战争之影"); Set(c, "buildPath", request(Json.D("action", "build_path", "hero", "战争之影",
                "itemIds", new int[] { 3161 }, "itemsKnown", true))); Render(c);
            Assert(Contents(content).Contains("核心顺序参考") && Contents(content).Contains("魔宗"), "Route evidence label missing");
            Save(content, title, Path.Combine(root, "artifacts", "cache", "items-hecarim.png"), 450);
            Set(c, "hero", "邪恶小法师"); Set(c, "buildPath", request(Json.D("action", "build_path", "hero", "邪恶小法师", "itemIds", new int[0], "itemsKnown", true)));
            Render(c);
            Assert(Contents(content).Contains("启发式") && Contents(content).Contains("卢登"), "Core step/evidence missing");
            Save(content, title, Path.Combine(root, "artifacts", "cache", "items-veigar.png"), 450);
            Set(c, "lastResult", Json.D("conditionalStatus", "available", "choices", new[] {
                Json.D("name", "条件样本", "hasStats", true, "winRate", .4, "conditional", Json.D("winRate", .7)) }));
            Set(c, "overlayShowChoices", true); Render(c);
            Assert(Contents(content).Contains("70.0%") && !Contents(content).Contains("40.0%"), "Overlay displayed marginal instead of conditional rate");
            Console.WriteLine("PASS: gameflow boundaries, fast-click buffering, stale-frame/reroll guards, conditional-rate rendering, opening/core routes; 4 PNGs.");
            return 0;
        } catch (Exception error) {
            Console.Error.WriteLine(error); return 1;
        } finally {
            if (c != null) ((GameClickListener)typeof(Controller).GetField("clickListener", Fields).GetValue(c)).Dispose();
            if (backend != null) backend.Dispose();
            if (liveBackend != null) liveBackend.Dispose();
        }
    }
}
