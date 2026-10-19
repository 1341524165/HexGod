using System;
using System.IO;
using System.Text;

namespace hexgod
{
    static class RuntimeLog
    {
        static readonly object Gate = new object();
        public static void Write(string directory, string stage, Exception error) {
            lock (Gate) {
                try {
                    string path = Path.Combine(directory, "runtime.log");
                    if (File.Exists(path) && new FileInfo(path).Length > 1024 * 1024) File.WriteAllText(path, "");
                    File.AppendAllText(path, DateTime.Now.ToString("s") + " [" + stage + "] " + error + Environment.NewLine, Encoding.UTF8);
                } catch { }
            }
        }
    }
}
