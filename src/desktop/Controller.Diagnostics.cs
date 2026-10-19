using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Interop;

namespace hexgod
{
    sealed partial class Controller
    {
        string lastConnectionSnapshot = "";
        string inputHealthSession = "";
        long nextInputHealthAt;
        string liveConnection = "waiting", lcuConnection = "waiting", detectedMode = "";
        [StructLayout(LayoutKind.Sequential)] struct WindowRect { public int Left, Top, Right, Bottom; }
        [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
        [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window, out WindowRect rect);

        void WriteConnectionSnapshot() {
            try {
                IntPtr handle = overlay == null ? IntPtr.Zero : new WindowInteropHelper(overlay).Handle;
                WindowRect rect = new WindowRect();
                bool onMonitor = false;
                if (handle != IntPtr.Zero && GetWindowRect(handle, out rect)) {
                    var bounds = System.Drawing.Rectangle.FromLTRB(rect.Left, rect.Top, rect.Right, rect.Bottom);
                    foreach (var screen in System.Windows.Forms.Screen.AllScreens)
                        if (screen.Bounds.IntersectsWith(bounds)) onMonitor = true;
                }
                int style = handle == IntPtr.Zero ? 0 : GetWindowLong(handle, -20);
                var gameBounds = GameWindowProbe.ClientBounds(liveGameHwnd);
                var data = Json.D("version", "0.18", "phase", session.Phase, "mode", detectedMode,
                    "matchLog", MatchLogKey, "clickListenerInstalled", clickListener.Installed, "clickListenerError", clickListener.Error,
                    "clickCaptureMethod", clickListener.Method, "mouseInputClicks", clickListener.ObservedClicks,
                    "rawInputPackets", clickListener.Packets, "lastRawInputAtMs", clickListener.LastPacketAt,
                    "buttonStatePolling", clickListener.Polling, "buttonStateChecks", clickListener.PollChecks,
                    "buttonStateEdges", clickListener.PollEdges, "buttonStateClicks", clickListener.FallbackClicks,
                    "rawInputClicks", clickListener.RawClicks, "lastButtonStatePollAtMs", clickListener.LastPollAt,
                    "lastInputSource", clickListener.LastSource,
                    "rawInputStalled", GameWindowProbe.IsForeground(liveGameHwnd) && NowMs() - clickListener.LastPacketAt > 1500,
                    "lastMouseClickAtMs", clickListener.LastClickAt, "lastClickForegroundHwnd", clickListener.LastForegroundWindow,
                    "listenerTargetHwnd", clickListener.GameWindow, "gameWindowHwnd", liveGameHwnd,
                    "outsideClientClicks", clickListener.OutsideClicks, "gameForegroundClicks", clickListener.ForegroundClicks,
                    "gameClientClicks", clickListener.AcceptedClicks,
                    "gameClientBounds", new[] { gameBounds.Left, gameBounds.Top, gameBounds.Right, gameBounds.Bottom },
                    "gameWindowPresent", liveGameHwnd > 0, "gameForeground", GameWindowProbe.IsForeground(liveGameHwnd),
                    "lcuConnection", lcuConnection, "liveConnection", liveConnection, "itemsKnown", liveItemsKnown,
                    "mainActive", Window.IsActive, "overlayRequested", overlayRequested,
                    "overlayVisible", handle != IntPtr.Zero && IsWindowVisible(handle), "overlayOnMonitor", onMonitor,
                    "overlayInsideGame", liveGameHwnd > 0 && gameBounds.Contains(System.Drawing.Rectangle.FromLTRB(rect.Left, rect.Top, rect.Right, rect.Bottom)),
                    "overlayRect", new[] { rect.Left, rect.Top, rect.Right, rect.Bottom },
                    "overlayTopmost", (style & 0x8) != 0, "overlayNoActivate", (style & 0x08000000) != 0,
                    "overlayClickThrough", (style & 0x20) != 0);
                data["observedAt"] = DateTime.Now.ToString("s");
                // Keep per-match history, including the zero-event case; a final
                // disconnected snapshot cannot explain where game clicks went.
                long now = NowMs();
                if (inputHealthSession != MatchLogKey || now >= nextInputHealthAt) {
                    inputHealthSession = MatchLogKey; nextInputHealthAt = now + 5000;
                    string folder = Path.Combine(ProjectPaths.LogDirectory(appDir), "matches", MatchLogKey);
                    Directory.CreateDirectory(folder);
                    string path = Path.Combine(folder, "input-health.jsonl");
                    if (File.Exists(path) && new FileInfo(path).Length > 2*1024*1024)
                        File.Copy(path, path + ".previous", true);
                    if (File.Exists(path) && new FileInfo(path).Length > 2*1024*1024) File.WriteAllText(path, "");
                    var health = Json.D("at", data["observedAt"], "version", "0.18", "method", clickListener.Method,
                        "installed", clickListener.Installed, "error", clickListener.Error,
                        "packets", clickListener.Packets, "lastPacketAtMs", clickListener.LastPacketAt,
                        "buttonStatePolling", clickListener.Polling, "buttonStateChecks", clickListener.PollChecks,
                        "buttonStateEdges", clickListener.PollEdges, "buttonStateClicks", clickListener.FallbackClicks,
                        "lastButtonStatePollAtMs", clickListener.LastPollAt, "lastInputSource", clickListener.LastSource,
                        "rawInputStalled", data["rawInputStalled"],
                        "mouseClicks", clickListener.ObservedClicks, "foregroundClicks", clickListener.ForegroundClicks,
                        "clientClicks", clickListener.AcceptedClicks, "outsideClientClicks", clickListener.OutsideClicks,
                        "targetHwnd", clickListener.GameWindow, "gameHwnd", liveGameHwnd,
                        "lastClickForegroundHwnd", clickListener.LastForegroundWindow,
                        "gameForeground", data["gameForeground"], "gameClientBounds", data["gameClientBounds"],
                        "automatic", automatic, "ready", ready, "panelVisible", choiceView.Visible,
                        "geometryReady", trackingGeometry != null, "queuedClick", queuedClick != null, "selectionPending", selectionPending);
                    File.AppendAllText(path, Json.New().Serialize(health) + Environment.NewLine, Encoding.UTF8);
                }
                string signature = Json.New().Serialize(data);
                if (signature == lastConnectionSnapshot) return;
                lastConnectionSnapshot = signature;
                File.WriteAllText(ProjectPaths.StateFile(appDir, "connection-status.json"), Json.New().Serialize(data), Encoding.UTF8);
            } catch (Exception error) { RuntimeLog.Write(appDir, "input_diagnostics", error); }
        }
    }
}
