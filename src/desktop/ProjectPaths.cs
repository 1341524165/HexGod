using System;
using System.IO;

namespace hexgod
{
    static class ProjectPaths
    {
        public static string FindRoot(string start) {
            string configured = Environment.GetEnvironmentVariable("HEXGOD_SOURCE_DIR");
            var directory = new DirectoryInfo(String.IsNullOrEmpty(configured) ? start : configured);
            while (directory != null) {
                if (File.Exists(Path.Combine(directory.FullName, "pyproject.toml")) &&
                    Directory.Exists(Path.Combine(directory.FullName, "src", "hexgod"))) return directory.FullName;
                if (!String.IsNullOrEmpty(configured)) break;
                directory = directory.Parent;
            }
            throw new DirectoryNotFoundException("未找到 hexgod 项目根目录，请保留完整项目结构。");
        }
        public static string LogDirectory(string root) {
            string path = Path.Combine(FindRoot(root), "var", "logs");
            Directory.CreateDirectory(path); return path;
        }
        public static string LogFile(string root, string name) { return Path.Combine(LogDirectory(root), name); }
        public static string StateFile(string root, string name) {
            string path = Path.Combine(FindRoot(root), "var", "state");
            Directory.CreateDirectory(path); return Path.Combine(path, name);
        }
    }
}
