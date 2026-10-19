using System;
using System.Runtime.InteropServices;
using System.Text;

namespace hexgod
{
    // Read-only HWND discovery. No dependency on LCU, OCR or champion lookup.
    static class GameWindowProbe
    {
        delegate bool EnumWindow(IntPtr window, IntPtr data);
        [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindow callback, IntPtr data);
        [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr window, StringBuilder text, int max);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr window, StringBuilder text, int max);
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
        [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr window, uint flags);
        [StructLayout(LayoutKind.Sequential)] struct NativeRect { public int Left, Top, Right, Bottom; }
        [StructLayout(LayoutKind.Sequential)] struct NativePoint { public int X, Y; }
        [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr window, out NativeRect rect);
        [DllImport("user32.dll")] static extern bool ClientToScreen(IntPtr window, ref NativePoint point);
        [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access, bool inherit, uint process);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder path, ref int length);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

        public static bool IsLocalFixture(long window) {
            var title = new StringBuilder(256);
            GetWindowText(new IntPtr(window), title, title.Capacity);
            return title.ToString().Contains("本地回归画面");
        }
        public static bool IsGame(IntPtr window) {
            if (window == IntPtr.Zero || !IsWindowVisible(window)) return false;
            uint pid; GetWindowThreadProcessId(window, out pid);
            IntPtr process = OpenProcess(0x1000, false, pid); // QUERY_LIMITED_INFORMATION only.
            if (process != IntPtr.Zero) {
                try {
                    var path = new StringBuilder(2048); int length = path.Capacity;
                    if (QueryFullProcessImageName(process, 0, path, ref length))
                        return System.IO.Path.GetFileName(path.ToString()).Equals("League of Legends.exe", StringComparison.OrdinalIgnoreCase);
                } finally { CloseHandle(process); }
            }
            // Elevated game processes can deny image lookup; class + exact game
            // title still distinguish the renderer from LeagueClientUx/lobby.
            var name = new StringBuilder(256); GetClassName(window, name, name.Capacity);
            var title = new StringBuilder(256); GetWindowText(window, title, title.Capacity);
            return name.ToString() == "RiotWindowClass" ||
                title.ToString().Equals("League of Legends (TM) Client", StringComparison.OrdinalIgnoreCase) ||
                title.ToString().Equals("League of Legends(TM) Client", StringComparison.OrdinalIgnoreCase);
        }

        public static long Find(long previous) {
            IntPtr foreground = GetForegroundWindow();
            if (IsGame(foreground)) return foreground.ToInt64();
            if (previous > 0 && IsGame(new IntPtr(previous))) return previous;
            long result = 0;
            EnumWindows((window, data) => { if (!IsGame(window)) return true; result = window.ToInt64(); return false; }, IntPtr.Zero);
            return result;
        }
        public static bool MatchesForeground(long window, long foreground) {
            return window > 0 && foreground > 0 && (window == foreground ||
                GetAncestor(new IntPtr(foreground), 2).ToInt64() == window) && !IsIconic(new IntPtr(window));
        }
        public static bool IsForeground(long window) {
            return MatchesForeground(window, GetForegroundWindow().ToInt64());
        }
        public static System.Drawing.Rectangle ClientBounds(long window) {
            NativeRect rect; NativePoint point = new NativePoint();
            var handle = new IntPtr(window);
            if (window <= 0 || !GetClientRect(handle, out rect) || !ClientToScreen(handle, ref point)) return System.Drawing.Rectangle.Empty;
            return new System.Drawing.Rectangle(point.X, point.Y, rect.Right - rect.Left, rect.Bottom - rect.Top);
        }
    }

    sealed partial class Controller
    {
        void RefreshGameWindow() {
            liveGameHwnd = GameWindowProbe.Find(liveGameHwnd); clickListener.GameWindow = liveGameHwnd;
            if (ready && automatic && !session.Active && liveGameHwnd == 0)
                Find<System.Windows.Controls.TextBlock>("SessionBadge").Text = "对局状态 · " + SessionLabel(session.Phase);
        }
        bool CanCaptureForeground() {
            return GameWindowProbe.IsForeground(liveGameHwnd) &&
                (!session.Observed || !session.Active || session.Supported);
        }
    }
}
