using System;
using System.Collections.Concurrent;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace hexgod
{
    // Two independent read-only sources: Raw Input and polled physical button
    // state. Neither captures, injects or suppresses input to the game.
    sealed class GameClickListener : IDisposable
    {
        internal sealed class Click { public int X, Y; public long Window, At; public string Source; }
        readonly ConcurrentQueue<Click> queue = new ConcurrentQueue<Click>();
        readonly ManualResetEvent initialized = new ManualResetEvent(false);
        readonly Thread thread;
        readonly Thread pollThread;
        readonly ManualResetEvent stopped = new ManualResetEvent(false);
        readonly ManualResetEvent pollInitialized = new ManualResetEvent(false);
        readonly MousePressTracker presses = new MousePressTracker();
        readonly object pressLock = new object();
        uint threadId;
        int installed;
        int polling;
        long pollChecks, pollEdges, fallbackClicks, rawClicks, lastPollAt;
        string lastSource = "none";
        long gameWindow, observedClicks, foregroundClicks, acceptedClicks;
        long packets, lastPacketAt, lastClickAt, lastForegroundWindow, outsideClicks;
        public string Error { get; private set; }
        public string Method { get { return "raw_input+button_state"; } }
        public bool Installed { get { return Volatile.Read(ref installed) != 0 || Polling; } }
        public bool Polling { get { return Volatile.Read(ref polling) != 0; } }
        public long PollChecks { get { return Interlocked.Read(ref pollChecks); } }
        public long PollEdges { get { return Interlocked.Read(ref pollEdges); } }
        public long FallbackClicks { get { return Interlocked.Read(ref fallbackClicks); } }
        public long RawClicks { get { return Interlocked.Read(ref rawClicks); } }
        public long LastPollAt { get { return Interlocked.Read(ref lastPollAt); } }
        public string LastSource { get { return lastSource; } }
        public long ObservedClicks { get { return Interlocked.Read(ref observedClicks); } }
        public long ForegroundClicks { get { return Interlocked.Read(ref foregroundClicks); } }
        public long AcceptedClicks { get { return Interlocked.Read(ref acceptedClicks); } }
        public long Packets { get { return Interlocked.Read(ref packets); } }
        public long LastPacketAt { get { return Interlocked.Read(ref lastPacketAt); } }
        public long LastClickAt { get { return Interlocked.Read(ref lastClickAt); } }
        public long LastForegroundWindow { get { return Interlocked.Read(ref lastForegroundWindow); } }
        public long OutsideClicks { get { return Interlocked.Read(ref outsideClicks); } }
        public long GameWindow {
            get { return Interlocked.Read(ref gameWindow); }
            set { Interlocked.Exchange(ref gameWindow, value); }
        }
        [StructLayout(LayoutKind.Sequential)] struct Point { public int X, Y; }
        [StructLayout(LayoutKind.Sequential)] struct Device { public ushort Page, Usage; public uint Flags; public IntPtr Window; }
        [StructLayout(LayoutKind.Sequential)] struct Header { public uint Type, Size; public IntPtr Device, W; }
        [StructLayout(LayoutKind.Sequential)] struct Message { public IntPtr Window; public uint Id; public UIntPtr W; public IntPtr L; public uint Time; public Point Point; public uint Private; }
        [DllImport("user32.dll", SetLastError=true)] static extern bool RegisterRawInputDevices(Device[] devices, uint count, uint size);
        [DllImport("user32.dll", SetLastError=true)] static extern uint GetRawInputData(IntPtr input, uint command, IntPtr data, ref uint size, uint headerSize);
        [DllImport("user32.dll")] static extern int GetMessage(out Message message, IntPtr window, uint min, uint max);
        [DllImport("user32.dll")] static extern IntPtr DispatchMessage(ref Message message);
        [DllImport("user32.dll")] static extern bool PostThreadMessage(uint thread, uint message, UIntPtr w, IntPtr l);
        [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] static extern bool GetCursorPos(out Point point);
        [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
        [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
        static long Now() { return (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalMilliseconds; }

        sealed class InputWindow : NativeWindow {
            readonly GameClickListener owner;
            readonly IntPtr buffer = Marshal.AllocHGlobal(64);
            public InputWindow(GameClickListener listener) {
                owner = listener;
                CreateHandle(new CreateParams { Caption = "hexgod passive mouse input", Parent = new IntPtr(-3) });
            }
            protected override void WndProc(ref System.Windows.Forms.Message message) {
                if (message.Msg == 0x00FF) {
                    try { owner.ReadInput(message.LParam, buffer); }
                    catch (Exception error) { owner.Error = error.Message; }
                }
                // WM_INPUT still goes to DefWindowProc for native cleanup.
                base.WndProc(ref message);
            }
            public void Close() { DestroyHandle(); Marshal.FreeHGlobal(buffer); }
        }
        public GameClickListener() {
            thread = new Thread(Run) { IsBackground = true, Name = "hexgod passive raw input" };
            thread.SetApartmentState(ApartmentState.STA);
            thread.Start();
            pollThread = new Thread(PollButtons) { IsBackground = true, Name = "hexgod passive button state" };
            pollThread.Start();
            if (!initialized.WaitOne(2000)) Error = "鼠标监听初始化超时";
            if (!pollInitialized.WaitOne(1000)) Error = "鼠标状态采集初始化超时";
        }
        void PollButtons() {
            Volatile.Write(ref polling, 1); pollInitialized.Set();
            try {
                while (!stopped.WaitOne(8)) {
                    long at = Now();
                    bool edge = presses.ObserveState((GetAsyncKeyState(0x01) & 0x8000) != 0);
                    Interlocked.Increment(ref pollChecks); Interlocked.Exchange(ref lastPollAt, at);
                    if (!edge) continue;
                    Interlocked.Increment(ref pollEdges);
                    // When WM_INPUT is healthy it remains the precise source.
                    // The observed Oct 19 failure had zero packets for 15 min.
                    if (at - LastPacketAt <= 250) continue;
                    RecordClick("button_state", at);
                }
            } catch (Exception error) { Error = error.Message; }
            finally { Volatile.Write(ref polling, 0); }
        }
        void Run() {
            InputWindow window = null;
            try {
                threadId = GetCurrentThreadId();
                window = new InputWindow(this);
                // INPUTSINK observes physical mouse input in the background.
                // Do not set NOLEGACY/CAPTUREMOUSE: the game keeps all its input.
                var devices = new[] { new Device { Page = 1, Usage = 2, Flags = 0x100, Window = window.Handle } };
                if (!RegisterRawInputDevices(devices, 1, (uint)Marshal.SizeOf(typeof(Device))))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                Volatile.Write(ref installed, 1); initialized.Set();
                Message message;
                while (GetMessage(out message, IntPtr.Zero, 0, 0) > 0) DispatchMessage(ref message);
            } catch (Exception error) { Error = error.Message; initialized.Set(); }
            finally {
                Volatile.Write(ref installed, 0);
                if (window != null) {
                    RegisterRawInputDevices(new[] { new Device { Page = 1, Usage = 2, Flags = 1 } }, 1, (uint)Marshal.SizeOf(typeof(Device)));
                    window.Close();
                }
            }
        }
        // RAWINPUTHEADER is 24 bytes on x64, 16 on x86. RAWMOUSE's union
        // starts at +4 (not +2); a left-button DOWN is the transition bit 1.
        internal static bool IsLeftButtonDown(IntPtr data, uint size) {
            int headerSize = Marshal.SizeOf(typeof(Header));
            return size >= headerSize + 24 && Marshal.ReadInt32(data) == 0 &&
                (Marshal.ReadInt16(data, headerSize + 4) & 1) != 0;
        }
        void ReadInput(IntPtr input, IntPtr buffer) {
            uint size = 64;
            uint read = GetRawInputData(input, 0x10000003, buffer, ref size, (uint)Marshal.SizeOf(typeof(Header)));
            if (read == uint.MaxValue) { Error = new Win32Exception(Marshal.GetLastWin32Error()).Message; return; }
            Interlocked.Increment(ref packets);
            long at = Now(); Interlocked.Exchange(ref lastPacketAt, at);
            if (!IsLeftButtonDown(buffer, read)) return;
            RecordClick("raw_input", at);
        }
        void RecordClick(string source, long at) {
            lock (pressLock) {
                if (!presses.Accept(source, at)) return;
            }
            lastSource = source;
            if (source == "button_state") Interlocked.Increment(ref fallbackClicks);
            else Interlocked.Increment(ref rawClicks);
            Interlocked.Increment(ref observedClicks); Interlocked.Exchange(ref lastClickAt, at);
            long window = GameWindow, foreground = GetForegroundWindow().ToInt64();
            Interlocked.Exchange(ref lastForegroundWindow, foreground);
            if (!GameWindowProbe.MatchesForeground(window, foreground)) return;
            Interlocked.Increment(ref foregroundClicks);
            Point point;
            if (!GetCursorPos(out point) || !GameWindowProbe.ClientBounds(window).Contains(point.X, point.Y)) {
                Interlocked.Increment(ref outsideClicks); return;
            }
            queue.Enqueue(new Click { X = point.X, Y = point.Y, Window = window, At = at, Source = source });
            Interlocked.Increment(ref acceptedClicks);
            Click unused;
            while (queue.Count > 128) queue.TryDequeue(out unused);
        }
        public bool TryRead(out Click click) { return queue.TryDequeue(out click); }
        public void Dispose() {
            stopped.Set();
            if (threadId != 0) PostThreadMessage(threadId, 0x0012, UIntPtr.Zero, IntPtr.Zero);
            thread.Join(1000);
            pollThread.Join(1000);
        }
    }
}
