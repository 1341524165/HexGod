# hexgod

Windows 海克斯大乱斗浮窗助手，WPF 桌面端与 Python OCR／推荐服务。当前版本 0.18。

## 启动与开发

双击项目最外层的 **hexgod.exe** 即可启动。请保留完整项目文件夹；程序会自动定位其中的后端、模型和数据。开发时也可执行：

```powershell
.\scripts\start.ps1
.\scripts\verify.ps1
```

整个目录可迁移：运行时、模型和数据均在项目内，不依赖旧安装目录。仅有 Git 源码副本时需补齐本地 runtime 和模型，见 [开发说明](docs/DEVELOPMENT.md)。

## 工程结构

| 目录 | 职责 |
|---|---|
| `src/hexgod` | 正式 Python 包：API、推荐、视觉、选择追踪、游戏接口 |
| `src/desktop` | WPF 源码、XAML、项目文件 |
| `tests` | 单元、集成、桌面测试及可追溯的回归素材 |
| `scripts` | 受支持的构建、验证、审计、数据同步入口 |
| `resources` | 校验后的生产数据、规则、英雄资料、原始快照、旧版测试数据 |
| `assets/models` | OCR 模型及来源说明 |
| `docs` | 架构、开发、运维、验证边界；历史资料位于 `history` |
| `build` / `artifacts` | 编译产物、截图、缓存、审计报告，Git 忽略 |
| `var` | 个人设置、状态和对局日志，Git 忽略 |
| `runtime` | 本地 Python 解释器和依赖，Git 忽略 |

根目录不放应用脚本、C# 源码或审计输出。Python 入口是 `python -m hexgod`；正常开发可 `pip install -e .`，随包解释器由 bootstrap 脚本作相对路径注册。

## 当前行为与验证边界

常规装备最多三件，综合胜率和场数排序；联动装备按实际候选显示名字，无候选时隐藏并收窄浮窗。指定装备买完后重新评估。统计来自 ARAMGG；目前没有装备与已选海克斯的完整联合胜率。

海克斯选择要求收到明确点击并在同一游戏窗口两帧确认面板消失；识别到选项不等于选中。输入使用 Raw Input 与独立只读按键状态采样，两路去重，不拦截或注入游戏输入。

[历史日志审计](docs/RECORDING-AUDIT.md)没有找到可验证的真实对局 confirmed 成功记录；唯一非空历史记录是本机测试。新增多日志、跨英雄回放用于验证输入送达之后的链路，不冒充实战送达证明。后续日志明确记录英雄和证据类型。

- [架构与依赖边界](docs/ARCHITECTURE.md)
- [开发、构建和工具](docs/DEVELOPMENT.md)
- [测试与证据规则](docs/TESTING.md)
- [运行与日志位置](docs/OPERATIONS.md)

默认本地运行，不上传日志。Git 仅保留源码、测试、数据快照和文档。origin 已连接 https://github.com/1341524165/HexGod；推送按用户指示执行。
