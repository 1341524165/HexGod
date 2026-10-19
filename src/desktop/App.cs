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
    static class Json
    {
        public static JavaScriptSerializer New() { return new JavaScriptSerializer { MaxJsonLength = 10000000 }; }
        public static Dictionary<string, object> D(params object[] pairs) {
            var d = new Dictionary<string, object>();
            for (int i = 0; i < pairs.Length; i += 2) d[(string)pairs[i]] = pairs[i + 1];
            return d;
        }
        public static Dictionary<string, object> Obj(object value) { return value as Dictionary<string, object> ?? D(); }
        public static object Get(Dictionary<string, object> d, string k) { object v; return d.TryGetValue(k, out v) ? v : null; }
        public static string S(Dictionary<string, object> d, string k) { return Convert.ToString(Get(d, k), CultureInfo.InvariantCulture) ?? ""; }
        public static double N(Dictionary<string, object> d, string k) { double n; return Double.TryParse(S(d, k), NumberStyles.Any, CultureInfo.InvariantCulture, out n) ? n : 0; }
        public static bool B(Dictionary<string, object> d, string k) { return Get(d, k) is bool && (bool)Get(d, k); }
        public static IEnumerable<object> A(Dictionary<string, object> d, string k) { return (Get(d, k) as IEnumerable<object>) ?? Enumerable.Empty<object>(); }
        public static string Percent(double n) { return (n * 100).ToString("0.0", CultureInfo.InvariantCulture) + "%"; }
        public static string Count(double n) { return n >= 10000 ? (n / 10000).ToString("0.0") + " 万" : n.ToString("N0"); }
    }

    static class Entry
    {
        [STAThread]
        public static void Main() {
            bool created;
            using (var mutex = new Mutex(true, "Local\\hexgod_MVP", out created)) {
                if (!created) { MessageBox.Show("hexgod 已在运行，请从任务栏打开。", "hexgod"); return; }
                try {
                    var app = new Application();
                    app.DispatcherUnhandledException += (s, e) => {
                        RuntimeLog.Write(AppDomain.CurrentDomain.BaseDirectory, "ui", e.Exception);
                        MessageBox.Show(e.Exception.Message + "\n详情已写入 runtime.log。", "hexgod"); e.Handled = true;
                    };
                    var controller = new Controller(AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar));
                    app.Run(controller.Window);
                } catch (Exception error) {
                    RuntimeLog.Write(AppDomain.CurrentDomain.BaseDirectory, "startup", error);
                    MessageBox.Show(error.ToString() + "\n详情已写入 runtime.log。", "hexgod 启动失败");
                }
            }
        }
    }

    sealed partial class Controller
    {
        public Window Window { get; private set; }
        readonly string appDir;
        readonly BackendClient backend;
        readonly BackendClient liveBackend;
        readonly DispatcherTimer autoTimer;
        readonly DispatcherTimer liveTimer;
        readonly DispatcherTimer clickTimer;
        readonly DispatcherTimer overlayTimer;
        readonly DispatcherTimer windowTimer;
        readonly List<Dictionary<string, object>> heroes = new List<Dictionary<string, object>>();
        readonly List<string> ownedAugments = new List<string>();
        readonly string[] currentChoices = new string[] { "", "", "" };
        string hero = "";
        string savedHero = "";
        string version = "";
        string jointDataStatus = "unavailable";
        bool jointContextAvailable;
        bool busy, ready, closing, automatic;
        long liveGameHwnd;
        Dictionary<string, object> trackingGeometry;
        long matchStartMs;
        bool liveSyncBusy;
        bool liveItemsKnown;
        readonly List<int> liveItemIds = new List<int>();
        Dictionary<string, object> liveContext;
        Dictionary<string, object> buildPath;
        bool overlayShowChoices;
        readonly AugmentViewState choiceView = new AugmentViewState();
        string lastOverlayMode = "";
        int buildRevision;
        int heroRevision;
        Window overlay;
        StackPanel overlayContent;
        TextBlock overlayTitle;
        Dictionary<string, object> lastResult;
        Dictionary<string, object> lastFrame;
        HwndSource hwnd;
        double overlayLeft = -1, overlayTop = -1;
        bool savedOverlayEnabled = true;

        [DllImport("user32.dll")] static extern bool RegisterHotKey(IntPtr hWnd, int id, uint modifiers, uint key);
        [DllImport("user32.dll")] static extern bool UnregisterHotKey(IntPtr hWnd, int id);
        [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
        [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] static extern bool GetCursorPos(out WinPoint point);
        [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(WinPoint point);
        [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr hWnd, uint flags);
        [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter,
            int x, int y, int width, int height, uint flags);
        [DllImport("user32.dll")] static extern bool SetWindowDisplayAffinity(IntPtr hWnd, uint affinity);
        [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hWnd, int attr, ref int val, int size);
        [StructLayout(LayoutKind.Sequential)] struct WinPoint { public int X, Y; }

        T Find<T>(string name) where T : FrameworkElement { return (T)Window.FindName(name); }
        static Brush BrushOf(string hex) { return (Brush)new BrushConverter().ConvertFromString(hex); }
        static long NowMs() { return (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalMilliseconds; }
        static TextBlock Text(string text, double size, string color, bool bold) {
            return new TextBlock { Text = text, FontSize = size, Foreground = BrushOf(color),
                FontWeight = bold ? FontWeights.Bold : FontWeights.Normal, TextWrapping = TextWrapping.Wrap };
        }
        static void Add(StackPanel panel, string value, double size, string color, bool bold, double top) {
            var text = Text(value, size, color, bold); text.Margin = new Thickness(0, top, 0, 0); panel.Children.Add(text);
        }

        public Controller(string path) {
            appDir = ProjectPaths.FindRoot(path);
            backend = new BackendClient(appDir);
            liveBackend = new BackendClient(appDir, 12000);
            using (var stream = File.OpenRead(Path.Combine(appDir, "src", "desktop", "MainWindow.xaml"))) Window = (Window)XamlReader.Load(stream);
            ReadSettings();
            autoTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(200) };
            autoTimer.Tick += async (s, e) => {
                if (!GameWindowProbe.IsForeground(liveGameHwnd) && choiceView.Visible) {
                    choiceView.Observe(Json.D("captureSkipped", true), NowMs());
                    if (!choiceView.Visible) { trackingGeometry = null; RenderOverlay(); }
                }
                if (!busy && ready && CanCaptureForeground() && NowMs() >= nextCaptureAt) {
                    await Recognize(null, false);
                }
            };
            liveTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
            liveTimer.Tick += async (s, e) => await PollLiveState();
            clickTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(20) };
            clickTimer.Tick += async (s, e) => await ObserveClick();
            overlayTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(500) };
            overlayTimer.Tick += (s, e) => EnsureOverlayOnTop();
            windowTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(750) };
            windowTimer.Tick += (s, e) => RefreshGameWindow();
            Find<TextBox>("HeroSearch").TextChanged += (s, e) => FilterHeroes();
            Find<ListBox>("HeroList").SelectionChanged += async (s, e) => {
                var selected = Find<ListBox>("HeroList").SelectedItem as ListBoxItem;
                if (selected != null) await SelectHero((string)selected.Tag);
            };
            Find<Button>("DemoButton").Click += async (s, e) => await RunAction("demo");
            Find<Button>("AddOwnedButton").Click += async (s, e) => await AddOwned(Find<ComboBox>("OwnedPicker").Text);
            Find<Button>("ClearOwnedButton").Click += async (s, e) => await ClearOwned();
            Find<ComboBox>("OwnedPicker").KeyDown += async (s, e) => {
                if (e.Key == Key.Enter) { e.Handled = true; await AddOwned(Find<ComboBox>("OwnedPicker").Text); }
            };
            Find<Button>("RankButton").Click += async (s, e) => {
                for (int i = 0; i < 3; i++) currentChoices[i] = Find<ComboBox>("Manual" + (i + 1)).Text;
                await RunAction("rank");
            };
            Find<Button>("CaptureButton").Click += async (s, e) => await Recognize(null, true);
            Find<Button>("ImportButton").Click += async (s, e) => {
                autoTimer.Stop();
                var dialog = new OpenFileDialog { Title = "选择完整游戏截图", Filter = "图片|*.png;*.jpg;*.jpeg;*.bmp;*.webp|所有文件|*.*" };
                if (dialog.ShowDialog(Window) == true) await Recognize(dialog.FileName, false);
                if (automatic) autoTimer.Start();
            };
            Find<Button>("AutoButton").Click += async (s, e) => await ToggleAuto();
            Find<Button>("OverlayButton").Click += (s, e) => ToggleOverlay();
            Window.SourceInitialized += (s, e) => InitializeNative();
            Find<Button>("LogButton").Click += (s, e) => {
                string folder = Path.Combine(ProjectPaths.LogDirectory(appDir), "matches", MatchLogKey);
                Directory.CreateDirectory(folder); Process.Start(new ProcessStartInfo(folder) { UseShellExecute = true });
            };
            Window.Closed += (s, e) => { closing = true; clickListener.Dispose(); autoTimer.Stop(); liveTimer.Stop(); clickTimer.Stop(); overlayTimer.Stop(); windowTimer.Stop(); SaveSettings(); if (overlay != null) overlay.Close(); if (hwnd != null) { UnregisterHotKey(hwnd.Handle, 1); UnregisterHotKey(hwnd.Handle, 2); } backend.Dispose(); liveBackend.Dispose(); };
            Window.Loaded += async (s, e) => await Initialize();
            EmptyChoices();
        }
        void InitializeNative() {
            hwnd = HwndSource.FromHwnd(new WindowInteropHelper(Window).Handle);
            hwnd.AddHook((IntPtr handle, int message, IntPtr wp, IntPtr lp, ref bool handled) => {
                if (message == 0x0312) {
                    if (wp.ToInt32() == 1 && !busy && ready) Window.Dispatcher.BeginInvoke(new Action(async () => await Recognize(null, true)));
                    if (wp.ToInt32() == 2) ToggleOverlay();
                    handled = true;
                }
                return IntPtr.Zero;
            });
            bool h = RegisterHotKey(hwnd.Handle, 1, 0x4003, 0x48);
            bool o = RegisterHotKey(hwnd.Handle, 2, 0x4003, 0x4F);
            if (!h || !o) Find<TextBlock>("FooterText").Text = "部分快捷键已被占用，可使用界面按钮。";
            try { int dark = 1; DwmSetWindowAttribute(hwnd.Handle, 20, ref dark, 4); } catch { }
        }
        async Task Initialize() {
            SetBusy(true);
            windowTimer.Start(); RefreshGameWindow();
            if (savedOverlayEnabled && overlay == null) ToggleOverlay();
            try {
                var catalog = await backend.Request(Json.D("action", "catalog"));
                heroes.AddRange(Json.A(catalog, "heroes").Select(Json.Obj));
                var meta = Json.Obj(Json.Get(catalog, "meta")); version = Json.S(meta, "version");
                jointDataStatus = Json.S(catalog, "jointDataStatus");
                Find<TextBlock>("DataBadge").Text = "ARAMGG " + Json.S(meta, "dataVersion") + " · " + Json.S(meta, "report_date");
                Find<TextBlock>("HeroCount").Text = heroes.Count + " 位英雄 · 按样本胜率排列";
                foreach (object preset in Json.A(catalog, "presets")) Find<ComboBox>("PresetCombo").Items.Add(Json.S(Json.Obj(preset), "key"));
                Find<ComboBox>("PresetCombo").SelectedItem = "1920x1080@100";
                foreach (object name in Json.A(catalog, "augments")) {
                    Find<ComboBox>("OwnedPicker").Items.Add(name);
                    for (int i = 1; i <= 3; i++) Find<ComboBox>("Manual" + i).Items.Add(name);
                }
                ready = true;
                FilterHeroes();
                string initial = heroes.Any(x => Json.S(x, "name") == savedHero) ? savedHero : "赏金猎人";
                var initialItem = Find<ListBox>("HeroList").Items.Cast<ListBoxItem>().FirstOrDefault(x => (string)x.Tag == initial);
                if (initialItem == null) initialItem = Find<ListBox>("HeroList").Items.Cast<ListBoxItem>().FirstOrDefault();
                Find<ListBox>("HeroList").SelectedItem = initialItem;
                if (initialItem != null) Find<ListBox>("HeroList").ScrollIntoView(initialItem);
                automatic = true;
                Find<Button>("AutoButton").Content = "停止自动识别与记录";
                autoTimer.Start(); liveTimer.Start(); clickTimer.Start();
                Status("已就绪。后台识别和点击记录已开启；选中英雄后可直接在游戏内选择海克斯。");
            } catch (Exception error) { Status("加载失败：" + error.Message, true); }
            finally { SetBusy(false); }
        }
        void FilterHeroes() {
            string search = Find<TextBox>("HeroSearch").Text.Trim();
            var list = Find<ListBox>("HeroList"); list.Items.Clear();
            foreach (var row in heroes.Where(h => (Json.S(h, "name") + Json.S(h, "fullName") + Json.S(h, "aliases")).IndexOf(search, StringComparison.OrdinalIgnoreCase) >= 0)) {
                var content = new StackPanel();
                Add(content, Json.S(row, "name"), 13, "#DCE8F5", true, 0);
                Add(content, Json.Percent(Json.N(row, "winRate")) + " 胜率 · " + Samples(row), 10, "#8FA6BE", false, 5);
                list.Items.Add(new ListBoxItem { Content = content, Tag = Json.S(row, "name") });
            }
            list.SelectedItem = list.Items.Cast<ListBoxItem>().FirstOrDefault(x => (string)x.Tag == hero);
        }
        async Task SelectHero(string selected) {
            if (hero == selected) return;
            hero = selected;
            ResetMatchContext();
            int revision = heroRevision;
            Find<TextBlock>("HeroTitle").Text = hero;
            Find<TextBlock>("HeroWinRate").Text = "—";
            Find<TextBlock>("HeroSubtitle").Text = "正在载入英雄统计…";
            EmptyChoices(); lastResult = null; RenderOverlay();
            try {
                var detail = await backend.Request(Json.D("action", "hero", "hero", selected, "owned", ownedAugments.ToArray()));
                if (revision != heroRevision || closing) return;
                Find<TextBlock>("HeroWinRate").Text = Json.Percent(Json.N(detail, "winRate"));
                Find<TextBlock>("HeroSubtitle").Text = Samples(detail) + "  ·  选取率 " + Json.Percent(Json.N(detail, "pickRate")) + "  ·  版本 " + version;
                RenderItems(detail);
                RenderOwned();
                await RefreshBuildPath();
                if (currentChoices.Any(x => x.Length > 0)) {
                    var rank = await backend.Request(Json.D("action", "rank", "hero", selected, "choices", currentChoices.ToArray(), "owned", ownedAugments.ToArray()));
                    if (revision == heroRevision && !closing) RenderResult(rank);
                }
            } catch (Exception error) { if (!closing) Status(error.Message, true); }
        }
        void RenderOwned() {
            Find<TextBlock>("OwnedSummary").Text = ownedAugments.Count == 0 ?
                "自动记录 0 项 · 等待在游戏中选取海克斯" :
                "已自动记录 " + ownedAugments.Count + " 项 · " + String.Join("、", ownedAugments.ToArray());
            var chips = Find<WrapPanel>("OwnedChips"); chips.Children.Clear();
            Find<TextBlock>("OwnedDataStatus").Text = clickListener.Installed ?
                "点击监听已开启 · 识别、刷新、点击和确认结果均写入本局日志。" :
                "点击监听未启动：" + clickListener.Error;
            RenderOverlay();
        }
        async Task AddOwned(string raw) {
            if (busy || !ready || hero.Length == 0) return;
            string value = (raw ?? "").Trim();
            if (value.Length == 0) { Status("请先输入本局已经选择的海克斯名称。", true); return; }
            await ChangeOwned(ownedAugments.Concat(new[] { value }).ToArray(), false);
        }
        async Task RemoveOwned(string name) {
            if (busy || !ready) return;
            await ChangeOwned(ownedAugments.Where(x => x != name).ToArray(), false);
        }
        async Task ClearOwned() {
            if (busy || !ready) return;
            ResetMatchContext();
            await ChangeOwned(new string[0], true);
        }
        Task ObserveClick() {
            GameClickListener.Click click;
            while (clickListener.TryRead(out click)) {
                lastHandledInputSource = click.Source;
                if (automatic && ready && !closing && queuedClick == null && !selectionPending)
                    BufferGameClick(click.X, click.Y, click.Window, click.At);
                else if (choiceView.Visible)
                    LogInput(selectionPending ? "confirmation_in_progress" : queuedClick != null ? "click_already_queued" : "tracking_disabled",
                        click.X, click.Y, click.At, 0);
            }
            return Task.FromResult(0);
        }
        async Task ChangeOwned(string[] proposed, bool newMatch) {
            int revision = ++heroRevision;
            queuedClick = null; bufferedClick = null; selectionPending = false; trackingGeometry = null;
            SetBusy(true);
            try {
                var detail = await backend.Request(Json.D("action", "hero", "hero", hero, "owned", proposed));
                if (revision != heroRevision || closing) return;
                bool added = proposed.Any(name => !ownedAugments.Contains(name));
                ownedAugments.Clear();
                ownedAugments.AddRange(Json.A(detail, "owned").Select(Convert.ToString));
                if (newMatch) choiceView.Reset();
                else if (added) choiceView.Selected(NowMs());
                overlayShowChoices = false;
                jointContextAvailable = Json.B(detail, "jointContextAvailable");
                RenderItems(detail);
                Find<ComboBox>("OwnedPicker").Text = "";
                if (newMatch) {
                    for (int i = 0; i < 3; i++) { currentChoices[i] = ""; Find<ComboBox>("Manual" + (i + 1)).Text = ""; }
                    lastResult = null; EmptyChoices();
                } else if (lastResult != null) {
                    var ranked = await backend.Request(Json.D("action", "rank", "hero", hero,
                        "choices", currentChoices.ToArray(), "owned", ownedAugments.ToArray(),
                        "source", Json.S(lastResult, "source")));
                    if (revision != heroRevision || closing) return;
                    RenderResult(ranked);
                }
                RenderOwned();
                await RefreshBuildPath();
                Status(newMatch ? "已清空本局记录，可开始新的一局。" : "已更新本局已选海克斯与出装参考。");
            } catch (Exception error) { if (!closing) Status("更新已选失败：" + error.Message, true); }
            finally { if (!closing) { SetBusy(false); RenderOwned(); } }
        }
        void SetBusy(bool value) {
            busy = value;
            foreach (string name in new[] { "CaptureButton", "ImportButton", "DemoButton", "RankButton", "AddOwnedButton", "ClearOwnedButton" }) Find<Button>(name).IsEnabled = ready && !value;
            Find<Button>("AutoButton").IsEnabled = ready && (!value || automatic);
            Find<ListBox>("HeroList").IsEnabled = !value;
            Find<ComboBox>("PresetCombo").IsEnabled = !value;
            Find<ComboBox>("OwnedPicker").IsEnabled = ready && !value;
        }
        void Status(string message, bool error = false) {
            Find<TextBlock>("StatusText").Text = message;
            Find<TextBlock>("StatusText").Foreground = BrushOf(error ? "#F4B18A" : "#A7B9D1");
        }
        void ReadSettings() {
            try { var d = Json.Obj(Json.New().DeserializeObject(File.ReadAllText(ProjectPaths.StateFile(appDir, "settings.json"), Encoding.UTF8)));
                savedHero = Json.S(d, "hero"); overlayLeft = Json.N(d, "overlayLeft"); overlayTop = Json.N(d, "overlayTop");
                if (d.ContainsKey("overlayEnabled")) savedOverlayEnabled = Json.B(d, "overlayEnabled"); } catch { }
        }
        void SaveSettings() {
            try { File.WriteAllText(ProjectPaths.StateFile(appDir, "settings.json"), Json.New().Serialize(Json.D("hero", hero, "overlayLeft", overlayLeft, "overlayTop", overlayTop, "overlayEnabled", overlayRequested)), Encoding.UTF8); } catch { }
        }
    }
}
