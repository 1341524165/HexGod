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
        async Task RunAction(string action) {
            if (busy || !ready || hero.Length == 0) return;
            int revision = heroRevision;
            SetBusy(true); Status("正在比较选项…");
            try {
                var result = await backend.Request(Json.D("action", action, "hero", hero,
                    "choices", currentChoices.ToArray(), "owned", ownedAugments.ToArray()));
                if (closing || revision != heroRevision) return;
                overlayShowChoices = action == "rank" && Json.N(result, "matchedCount") > 0;
                RenderResult(result);
            }
            catch (Exception error) { if (!closing) Status(error.Message, true); }
            finally { SetBusy(false); }
        }
        async Task Recognize(string image, bool switchToGame) {
            if (busy || !ready || hero.Length == 0) return;
            bool passive = automatic && image == null && !switchToGame;
            if (passive && !CanCaptureForeground()) return;
            int revision = image != null ? ++heroRevision : heroRevision;
            if (image != null) { queuedClick = bufferedClick = trackingGeometry = null; selectionPending = false; }
            if (passive) busy = true;
            else { SetBusy(true); Status(image == null ? "准备识别游戏画面…" : "正在识别截图，首次运行需要加载模型…"); }
            bool restore = switchToGame && Window.IsActive;
            bool overlayVisible = overlay != null && overlay.IsVisible;
            bool hideOverlayForCapture = overlayVisible && (!automatic || image != null || switchToGame);
            var oldState = Window.WindowState;
            string automaticallySelected = "";
            string revokedSelection = "";
            try {
                if (hideOverlayForCapture) { captureHidesOverlay = true; overlay.Hide(); }
                if (restore) { Window.WindowState = WindowState.Minimized; await Task.Delay(2000); }
                if (revision != heroRevision || closing) return;
                if (image == null && queuedClick != null) {
                    var click = queuedClick; queuedClick = null;
                    click["contextId"] = MatchLogKey + "|" + hero;
                    click["sessionKey"] = MatchLogKey;
                    var pending = await backend.Request(click);
                    if (revision != heroRevision || closing) return;
                    selectionPending = Json.S(pending, "pending").Length > 0;
                    if (selectionPending) Status("已捕获选项点击，等待画面消失确认…");
                }
                bool openingProbe = liveContext != null && Json.N(liveContext, "gameTime") < 90 && ownedAugments.Count == 0;
                var result = await backend.Request(Json.D("action", "ocr", "hero", hero, "image", image,
                    "preset", Convert.ToString(Find<ComboBox>("PresetCombo").SelectedItem), "owned", ownedAugments.ToArray(),
                    "hoverHistory", hoverHistory.ToArray(),
                    "probeTitles", openingProbe || choiceView.Visible || selectionPending, "contextId", image != null ? "offline|" + revision : MatchLogKey + "|" + hero,
                    "sessionKey", MatchLogKey, "evidenceKind", image != null ? "offline" :
                        GameWindowProbe.IsLocalFixture(liveGameHwnd) ? "desktop_fixture" : "live"));
                if (revision != heroRevision || closing) return;
                lastFrame = result;
                if (image == null) {
                    if (choiceView.SelectionAt > 0 && Json.N(result, "capturedAtMs") <= choiceView.SelectionAt) return;
                    long seenHwnd = (long)Json.N(result, "gameWindowHwnd");
                    if (seenHwnd > 0) { liveGameHwnd = seenHwnd; clickListener.GameWindow = seenHwnd; }
                    trackingGeometry = Json.Obj(Json.Get(result, "trackingGeometry"));
                    if (trackingGeometry.Count == 0) trackingGeometry = null;
                    InvalidateRerolledSlots(trackingGeometry);
                    CorrelateBufferedClick(trackingGeometry);
                    automaticallySelected = Json.S(result, "autoSelection");
                    revokedSelection = Json.S(result, "revokedSelection");
                    selectionPending = Json.B(result, "selectionPending");
                    overlayShowChoices = false;
                    choiceView.Observe(result, NowMs());
                    if (!choiceView.Visible) trackingGeometry = null;
                } else {
                    trackingGeometry = null; queuedClick = null; selectionPending = false;
                    choiceView.Reset();
                    overlayShowChoices = Json.N(result, "matchedCount") > 0;
                }
                if (automatic && Json.B(result, "captureSkipped")) {
                    if (lastResult == null) Status("等待 LoL 游戏窗口进入前台…");
                } else if (automatic && Json.N(result, "matchedCount") == 0 && lastResult != null) {
                    Status(Json.S(result, "message") + " 上次结果保留，时间见卡片上方。");
                } else if (!passive || !SameScreenChoices(result)) RenderResult(result);
                if (automaticallySelected.Length > 0 && !Json.B(result, "augmentPanelVisible")) choiceView.Selected(NowMs());
                RenderOverlay();
            }
            catch (Exception error) { RuntimeLog.Write(appDir, "ocr", error); if (!closing) Status("识别失败：" + error.Message, true); }
            finally {
                if (!closing) {
                    if (restore) Window.WindowState = oldState;
                    captureHidesOverlay = false;
                    if (hideOverlayForCapture && overlay != null) UpdateOverlayVisibility();
                    nextCaptureAt = queuedClick != null ? 0 : NowMs() + (choiceView.Visible || selectionPending ? 200 : 800);
                    if (passive) busy = false; else SetBusy(false);
                }
            }
            if (automatic && revokedSelection.Length > 0 && !closing && revision == heroRevision) {
                choiceView.Reset();
                if (lastFrame != null) choiceView.Observe(lastFrame, NowMs());
                await RemoveOwned(revokedSelection);
                Status("选项重新出现，已撤销此前的推测：" + revokedSelection);
            }
            if (automatic && automaticallySelected.Length > 0 && !closing && revision == heroRevision) {
                await AddOwned(automaticallySelected, Json.B(lastFrame, "augmentPanelVisible"));
                if (ownedAugments.Contains(automaticallySelected)) Status((Json.S(lastFrame, "selectionSource") == "hover" ? "根据鼠标位置推测选择：" : "已自动记录你选择的海克斯：") + automaticallySelected);
            }
        }
        async Task ToggleAuto() {
            automatic = !automatic;
            Find<Button>("AutoButton").Content = automatic ? "停止自动识别与记录" : "开始自动识别与记录";
            if (automatic) { autoTimer.Start(); liveTimer.Start(); clickTimer.Start(); Status("自动识别与点击记录已开启。请切回游戏；在其他窗口时会跳过。"); }
            else {
                autoTimer.Stop(); liveTimer.Stop(); clickTimer.Stop();
                ++heroRevision; queuedClick = null; selectionPending = false;
                liveGameHwnd = 0; trackingGeometry = null; bufferedClick = null;
                choiceView.Reset(); overlayShowChoices = false; RenderOverlay();
                try { await backend.Request(Json.D("action", "reset_tracking")); } catch { }
                Status("自动识别与点击记录已停止。");
            }
        }
    }

}
