# 0.10.1：启动、游戏发现与浮窗修复

## 已确认原因

- 0.10 的模式白名单只接受 ARAM，拒绝了海克斯大乱斗的 KIWI。参考源码 `reference/aramgg_client/src/main/services/match-history/statistics.ts` 明确使用 KIWI。
- 浮窗显示依赖主窗口激活，或游戏 HWND、模式和英雄匹配全部成功；API 不可用时切进游戏便会隐藏。
- `CaptureSkipped` 继承 RuntimeError，旧异常分支把正常的前台切换跳过重新抛出。backend.log 中已有这一异常的实证。
- 切换英雄/清空本局会抹掉游戏 HWND。窗口发现依赖固定标题和最小 1000×700 客户区，缺少进程路径识别。
- LCU 凭据发现依赖 WMI 命令行查询；访问失败没有独立进程/lockfile 路径回退。
- .NET Framework 显式构造子进程环境时，启动器传入大小写重复的环境变量可能导致异常；上一版测试绕开了这个环境，正式进程没有。

## 改动

- 支持 KIWI；优先使用运行中游戏的模式，活跃窗口/Live 数据可以覆盖滞后的大厅阶段。
- 独立 HWND 轮询、进程名识别、受限进程查询和窗口类/标题回退。最小有效截图放宽到 640×360。
- 浮窗开关与模式/接口/英雄解耦，默认开启并保存偏好。主窗口失焦后保留浮窗且穿透鼠标；重新置顶不激活窗口。
- 浮窗跑到游戏区域外时自动移回游戏内，适配跨屏和窗口模式。
- 正常跳过截图不再报错；窗口发现不依赖 OCR；接口未连上时继续使用手动英雄识别画面，装备库存保持明确的未同步状态。
- LCU 优先使用 Toolhelp/QueryFullProcessImageName 找到运行中进程附近的 lockfile，WMI 作为补充。
- 子进程使用原生环境继承和 `-X utf8`。新增 `runtime.log` 和不含账户/令牌的 `connection-status.json`。
- 统一入口 `hexgod.exe`；同一二进制备份 `hexgod-0.10.1.exe`。旧 0.10 不再作为推荐启动入口。

## 验证（2026-09-28）

- 96 项 Python 回归通过；WPF 状态与渲染测试通过，覆盖 KIWI、接口断开、滞后大厅、正常截图跳过及 lockfile 路径发现。
- 正式 EXE 实际启动，加载 173 位英雄，无启动错误。
- 主窗口最小化：原生 HWND 仍可见、在屏幕内、置顶、NOACTIVATE、鼠标穿透。
- 独立测试程序 `.cache/desktop-fixture/League of Legends.exe` 展示参考截图。它是本地 WinForms 测试画面，不是真实游戏。其标题不使用游戏标题，验证按进程名发现窗口。
- LCU 和 Live API 均断开时，测试进程进入前台后自动截图，识别出“未收录：裁决使 / 超凡邪恶 / 神射法师”，并切换到海克斯浮窗。
- 最终构建将浮窗移入测试窗口区域；快捷键隐藏/恢复通过，前台仍为测试进程。原生状态存于 `.cache/desktop-foreground-verified.json`、`desktop-hidden-verified.json` 和 `desktop-restored-verified.json`。
- 测试结束正常关闭测试进程，保留正式助手运行。

## 未验证边界

当前没有真实对局。真实游戏的 Live API 库存同步、反作弊环境下窗口行为，以及独占全屏覆盖效果尚不能据此确认。上述桌面验证不等于实局验收。

运行测试画面的源码为 `tests/DesktopGameFixture.cs`，构建脚本为 `tests/build_desktop_fixture.ps1`；不随正常启动加载，不修改游戏、账户或配置。
