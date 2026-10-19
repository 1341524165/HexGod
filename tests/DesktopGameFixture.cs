using System;
using System.Drawing;
using System.Windows.Forms;

// Compile only in .cache/desktop-fixture as League of Legends.exe. This is
// a local screenshot fixture, not a game, injection, or production dependency.
static class DesktopGameFixture
{
    [STAThread]
    static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        string path = args.Length > 0 ? args[0] : System.IO.Path.GetFullPath(System.IO.Path.Combine(
            AppDomain.CurrentDomain.BaseDirectory, "../../../reference/aramgg_client/tests/fixtures/augment-ocr/full-three-cards.png"));
        using (var image = Image.FromFile(path))
        using (var window = new Form {
            Text = "hexgod 本地回归画面（非游戏）",
            ClientSize = new Size(1280, 720), StartPosition = FormStartPosition.Manual,
            BackgroundImage = image, BackgroundImageLayout = ImageLayout.Stretch,
            FormBorderStyle = FormBorderStyle.None, AutoScaleMode = AutoScaleMode.None,
            MaximizeBox = false, KeyPreview = true
        }) {
            var screens = Screen.AllScreens;
            int screenIndex = Array.FindIndex(screens, screen => screen.Primary);
            window.Bounds = screens[screenIndex].Bounds;
            var label = new Label { Text = "本地测试画面 · F6 换屏 · R 重置 · 点击卡片模拟选择 · Esc 退出", AutoSize = true,
                Left = 20, Top = 20, ForeColor = Color.White, BackColor = Color.Black, Font = new Font("Microsoft YaHei UI", 14) };
            window.Controls.Add(label);
            window.KeyDown += (sender, e) => {
                if (e.KeyCode == Keys.F6) { screenIndex = (screenIndex+1)%screens.Length; window.Bounds = screens[screenIndex].Bounds; }
                if (e.KeyCode == Keys.R) window.BackgroundImage = image;
                if (e.KeyCode == Keys.Escape) window.Close();
            };
            window.MouseUp += (sender, e) => {
                if (e.Button == MouseButtons.Left && e.Y > window.ClientSize.Height*.22 && e.Y < window.ClientSize.Height*.64)
                    window.BackgroundImage = null;
            };
            Application.Run(window);
        }
    }
}
