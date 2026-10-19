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
    sealed class BackendClient : IDisposable
    {
        readonly string appDir;
        readonly int timeoutMs;
        readonly SemaphoreSlim gate = new SemaphoreSlim(1, 1);
        Process process;
        int counter;
        bool disposed;
        public BackendClient(string path, int timeout = 60000) { appDir = path; timeoutMs = timeout; }
        void Start() {
            if (disposed) throw new ObjectDisposedException("BackendClient");
            if (process != null && !process.HasExited) return;
            string source = Environment.GetEnvironmentVariable("HEXGOD_SOURCE_DIR") ?? appDir;
            string python = Path.Combine(source, "runtime", "python.exe");
            if (!File.Exists(python)) throw new FileNotFoundException("未找到项目内 runtime\\python.exe，请保留完整的 hexgod 目录。", python);
            string fixture = Environment.GetEnvironmentVariable("HEXGOD_TEST_LEGACY") == "1" ? " --legacy-fixture" : "";
            var info = new ProcessStartInfo(python, "-X utf8 -B -u \"" + Path.Combine(appDir, "backend.py") + "\"" + fixture) {
                WorkingDirectory = appDir, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            // Inherit the native environment without materializing Framework's
            // case-insensitive dictionary (launchers may supply both Path/PATH).
            process = new Process { StartInfo = info };
            process.ErrorDataReceived += (s, e) => {
                if (e.Data == null) return;
                try {
                    string logPath = Path.Combine(appDir, "backend.log");
                    if (File.Exists(logPath) && new FileInfo(logPath).Length > 1024 * 1024) File.WriteAllText(logPath, "", Encoding.UTF8);
                    File.AppendAllText(logPath, DateTime.Now.ToString("s") + " " + e.Data + Environment.NewLine, Encoding.UTF8);
                } catch { }
            };
            process.Start();
            process.StandardInput.AutoFlush = true;
            process.BeginErrorReadLine();
        }
        public async Task<Dictionary<string, object>> Request(Dictionary<string, object> request) {
            await gate.WaitAsync();
            try {
                Start();
                var activeProcess = process;
                request["id"] = ++counter;
                // ASCII JSON escapes avoid the .NET Framework stdin writer's system code page.
                string raw = Json.New().Serialize(request);
                var ascii = new StringBuilder();
                foreach (char c in raw) ascii.Append(c > 127 ? "\\u" + ((int)c).ToString("x4") : c.ToString());
                await activeProcess.StandardInput.WriteLineAsync(ascii.ToString());
                var read = activeProcess.StandardOutput.ReadLineAsync();
                if (await Task.WhenAny(read, Task.Delay(timeoutMs)) != read) { Stop(); throw new TimeoutException("本地服务超时。可以重试，服务会自动重启。"); }
                string line = await read;
                if (line == null) { Stop(); throw new IOException("本地识别服务已退出。请重试，详情见 backend.log。"); }
                var response = Json.Obj(Json.New().DeserializeObject(line));
                if ((int)Json.N(response, "id") != counter) { Stop(); throw new IOException("本地响应顺序异常，请重试。"); }
                if (!Json.B(response, "ok")) throw new InvalidOperationException(Json.S(response, "error"));
                return Json.Obj(Json.Get(response, "result"));
            }
            catch (IOException) { Stop(); throw; }
            finally { gate.Release(); }
        }
        void Stop() {
            var stopped = Interlocked.Exchange(ref process, null);
            if (stopped == null) return;
            try { if (!stopped.HasExited) stopped.Kill(); } catch { }
            stopped.Dispose();
        }
        public void Dispose() { disposed = true; Stop(); }
    }


}
