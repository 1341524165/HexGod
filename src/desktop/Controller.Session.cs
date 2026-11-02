using System;
using System.Collections.Generic;
using System.Linq;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;

namespace hexgod
{
    sealed partial class Controller
    {
        readonly GameSessionState session = new GameSessionState();
        readonly GameClickListener clickListener = new GameClickListener();
        readonly string runKey = DateTime.Now.ToString("yyyyMMdd-HHmmss");
        string MatchLogKey { get { return runKey + "-" + session.Generation; } }
        long nextCaptureAt;
        Dictionary<string, object> queuedClick;
        Dictionary<string, object> bufferedClick;
        Dictionary<string, object> hoverCandidate;
        bool selectionPending;
        bool liveHeroUnknown;
        string lastHandledInputSource = "unknown";
        readonly long[] slotInvalidatedAt = new long[3];

        static bool ContainsPoint(object raw, int x, int y) {
            var box = ((System.Collections.IEnumerable)raw).Cast<object>().Select(Convert.ToDouble).ToArray();
            return box.Length == 4 && x >= box[0] && x <= box[2] && y >= box[1] && y <= box[3];
        }
        void LogInput(string kind, int x, int y, long at, int slot) {
            try {
                string folder = Path.Combine(ProjectPaths.LogDirectory(appDir), "matches", MatchLogKey);
                Directory.CreateDirectory(folder);
                string path = Path.Combine(folder, "input.jsonl");
                if (File.Exists(path) && new FileInfo(path).Length > 1024*1024) File.WriteAllText(path, "");
                File.AppendAllText(path, Json.New().Serialize(Json.D("event", kind, "atMs", at,
                    "x", x, "y", y, "slot", slot, "windowHwnd", liveGameHwnd,
                    "method", clickListener.Method, "inputSource", lastHandledInputSource,
                    "geometryAtMs", trackingGeometry == null ? 0 : Json.N(trackingGeometry, "capturedAtMs"))) + Environment.NewLine, Encoding.UTF8);
            } catch (Exception error) { RuntimeLog.Write(appDir, "input_log", error); }
        }
        void InvalidateRerolledSlots(Dictionary<string, object> geometry) {
            if (geometry == null) return;
            var names = Json.A(geometry, "names").ToArray();
            for (int i = 0; i < Math.Min(3, names.Length); i++)
                if (Json.N(geometry, "capturedAtMs") <= slotInvalidatedAt[i]) names[i] = null;
            geometry["names"] = names;
        }

        void ResetMatchContext() {
            ++heroRevision; ++buildRevision; // Invalidate before any await.
            choiceView.Reset();
            trackingGeometry = null; queuedClick = null; bufferedClick = null; hoverCandidate = null; selectionPending = false;
            Array.Clear(slotInvalidatedAt, 0, slotInvalidatedAt.Length);
            // The game window outlives hero/round resets and is independently observed.
            matchStartMs = 0; nextCaptureAt = 0;
            liveHeroUnknown = false;
            liveItemsKnown = false; liveItemIds.Clear(); liveContext = null; buildPath = null;
            overlayShowChoices = false; ownedAugments.Clear(); lastResult = null; lastFrame = null;
            for (int i = 0; i < 3; i++) {
                currentChoices[i] = "";
                Find<ComboBox>("Manual" + (i + 1)).Text = "";
            }
            EmptyChoices(); RenderOwned(); RenderOverlay();
            Find<UniformGrid>("ItemCards").Children.Clear();
            Find<UniformGrid>("SynergyItemCards").Children.Clear();
            Find<StackPanel>("SynergyItemSection").Visibility = System.Windows.Visibility.Collapsed;
            Find<TextBlock>("BuildPath").Text = "本局状态已清空，等待重新同步。";
        }

        void SampleHover() {
            WinPoint point;
            if (!automatic || !ready || closing || !GameWindowProbe.IsForeground(liveGameHwnd)) {
                hoverCandidate = null; return;
            }
            if (hoverCandidate != null && NowMs() - Json.N(hoverCandidate, "atMs") > 1500) hoverCandidate = null;
            if (trackingGeometry == null || !GetCursorPos(out point)) return;
            if (Json.A(trackingGeometry, "rerollHitboxes").Any(raw => ContainsPoint(raw, point.X, point.Y))) {
                hoverCandidate = null; return;
            }
            if (!Json.A(trackingGeometry, "hitboxes").Any(raw => ContainsPoint(raw, point.X, point.Y))) return;
            hoverCandidate = Json.D("x", point.X, "y", point.Y, "hwnd", liveGameHwnd,
                "atMs", NowMs(), "geometry", trackingGeometry);
        }

        void BufferGameClick(int x, int y, long window, long at) {
            if (trackingGeometry != null) {
                var rerolls = Json.A(trackingGeometry, "rerollHitboxes").ToArray();
                for (int i = 0; i < Math.Min(3, rerolls.Length); i++) if (ContainsPoint(rerolls[i], x, y)) {
                    slotInvalidatedAt[i] = at; InvalidateRerolledSlots(trackingGeometry);
                    LogInput("reroll_click", x, y, at, i+1); nextCaptureAt = 0; return;
                }
                bool onCard = Json.A(trackingGeometry, "hitboxes").Any(raw => ContainsPoint(raw, x, y));
                if (!onCard) { LogInput("outside_card", x, y, at, 0); nextCaptureAt = 0; return; }
                LogInput("card_click", x, y, at, 0);
            } else if (choiceView.Visible) LogInput("click_without_geometry", x, y, at, 0);
            bufferedClick = Json.D("action", "click", "x", x, "y", y, "hwnd", window, "atMs", at,
                "hero", hero, "evidenceKind", GameWindowProbe.IsLocalFixture(window) ? "desktop_fixture" : "live",
                "inputSource", lastHandledInputSource);
            CorrelateBufferedClick(trackingGeometry);
        }

        void CorrelateBufferedClick(Dictionary<string, object> geometry) {
            if (bufferedClick == null || geometry == null || queuedClick != null || selectionPending) return;
            long elapsed = (long)Json.N(bufferedClick, "atMs") - (long)Json.N(geometry, "capturedAtMs");
            if (Json.N(bufferedClick, "hwnd") == Json.N(geometry, "windowHwnd") && elapsed >= 0 && elapsed <= 5000) {
                queuedClick = bufferedClick;
                queuedClick["geometry"] = geometry;
                bufferedClick = null; nextCaptureAt = 0;
            } else {
                LogInput(elapsed < 0 ? "awaiting_pre_click_frame" : elapsed > 5000 ? "stale_geometry" : "geometry_window_mismatch",
                    (int)Json.N(bufferedClick, "x"), (int)Json.N(bufferedClick, "y"), (long)Json.N(bufferedClick, "atMs"), 0);
            }
        }

        async Task PollLiveState() {
            if (liveSyncBusy || !automatic || !ready || closing) return;
            liveSyncBusy = true;
            try { await SyncLiveState(); }
            catch (Exception error) { if (!closing) Status("对局同步失败：" + error.Message, true); }
            finally { liveSyncBusy = false; }
        }

        async Task SyncLiveState() {
            Dictionary<string, object> live;
            try { live = await liveBackend.Request(Json.D("action", "live_state")); }
            catch (Exception error) {
                RuntimeLog.Write(appDir, "live_sync", error);
                if (!closing) Find<TextBlock>("SessionBadge").Text = "本地接口重连中 · 画面识别仍可用";
                return;
            }
            if (closing || !automatic) return;
            var lcu = Json.Obj(Json.Get(live, "lcu"));
            lcuConnection = Json.B(lcu, "available") ? "connected" : Json.S(lcu, "connection");
            liveConnection = Json.B(live, "available") ? "connected" : Json.S(live, "connection");
            detectedMode = Json.S(live, "gameMode");
            if (detectedMode.Length == 0) detectedMode = Json.S(lcu, "gameMode");
            RefreshGameWindow();
            if (liveGameHwnd > 0) {
                live["gameWindowPresent"] = true;
                live["gameWindowHwnd"] = liveGameHwnd;
            }
            if (session.Update(live, NowMs())) {
                ResetMatchContext();
                LogOverlayTransition(session.Active ? "match_started" : "match_ended", null);
                int revision = heroRevision;
                if (hero.Length > 0) {
                    var detail = await liveBackend.Request(Json.D("action", "hero", "hero", hero, "owned", new string[0]));
                    if (closing || revision != heroRevision || !automatic) return;
                    RenderItems(detail);
                    await RefreshBuildPath();
                    if (closing || revision != heroRevision || !automatic) return;
                }
                Status(session.Active ? "已发现游戏，等待画面识别与装备同步。" : "对局已结束，等待下一局。");
            }
            Find<TextBlock>("SessionBadge").Text = !session.Supported && session.Active ?
                "当前模式 " + detectedMode + " · 可手动识别" : "对局状态 · " + SessionLabel(session.Phase);
            if (!session.Active || !session.Supported) { UpdateOverlayVisibility(); return; }
            long seenWindow = (long)Json.N(live, "gameWindowHwnd");
            if (seenWindow > 0) liveGameHwnd = seenWindow;
            if (!Json.B(live, "available")) {
                Find<TextBlock>("SessionBadge").Text += " · 装备接口未连接，按所选英雄推荐";
                liveItemsKnown = false; liveItemIds.Clear(); liveContext = null;
                await RefreshBuildPath(); return;
            }
            string detectedHero = Json.S(live, "hero");
            bool unknown = detectedHero.Length == 0;
            if (unknown != liveHeroUnknown) {
                ++heroRevision; ++buildRevision;
                queuedClick = bufferedClick = trackingGeometry = null; selectionPending = false;
                choiceView.Reset(); overlayShowChoices = false;
            }
            liveHeroUnknown = unknown;
            if (unknown) Find<TextBlock>("SessionBadge").Text = "游戏已连接 · 英雄未匹配，沿用手动选择";
            if (detectedHero.Length > 0 && detectedHero != hero) {
                await SelectHero(detectedHero);
                if (closing || !automatic) return;
                Find<ListBox>("HeroList").SelectedItem = Find<ListBox>("HeroList").Items.Cast<ListBoxItem>()
                    .FirstOrDefault(x => (string)x.Tag == detectedHero);
                Status("已从游戏同步英雄：" + detectedHero);
            }
            if (detectedHero != hero) {
                liveItemsKnown = false; liveItemIds.Clear(); liveContext = null;
                await RefreshBuildPath(); return;
            }
            if (matchStartMs == 0) matchStartMs = (long)Json.N(live, "matchStartMs");
            live["matchStartMs"] = matchStartMs;
            live["sessionId"] = session.Generation;
            liveItemsKnown = Json.B(live, "itemsKnown");
            liveItemIds.Clear(); liveItemIds.AddRange(Json.A(live, "itemIds").Select(Convert.ToInt32));
            liveContext = live;
            await RefreshBuildPath(); UpdateOverlayVisibility();
        }

        static string SessionLabel(string phase) {
            switch (phase) {
                case "ChampSelect": return "英雄选择";
                case "GameStart": return "载入对局";
                case "InProgress": return "游戏中";
                case "LiveFallback": return "游戏中（本地接口 / 窗口）";
                case "Reconnect": case "Reconnecting": return "等待重连";
                case "WaitingForStats": case "PreEndOfGame": case "EndOfGame": return "对局结束";
                case "Lobby": return "大厅";
                case "Matchmaking": case "ReadyCheck": return "等待对局";
                default: return "等待游戏";
            }
        }

        async Task RefreshBuildPath() {
            if (hero.Length == 0 || closing) return;
            int revision = heroRevision;
            int requestRevision = ++buildRevision;
            try {
                var path = await liveBackend.Request(Json.D("action", "build_path", "hero", hero,
                    "owned", ownedAugments.ToArray(), "itemIds", liveItemIds.ToArray(),
                    "itemsKnown", liveItemsKnown, "liveContext", liveContext));
                if (revision == heroRevision && requestRevision == buildRevision && !closing) {
                    buildPath = path; RenderEquipmentCards(path); RenderOverlay();
                }
            } catch (Exception error) { if (!closing) Status("出装建议更新失败：" + error.Message, true); }
        }
    }
}
