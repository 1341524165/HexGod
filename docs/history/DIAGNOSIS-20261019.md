# 10 月 19 日虚幻武器选择未入账

对应本机日志：`logs/matches/20261019-201451-3/`，20:43:11～20:58:58。

20:43:32～20:43:38 连续识别到“循环往复／虚幻武器／超强大脑”，中间卡片物理点击范围为 `[1082,265,1477,922]`，游戏 HWND 为 396406。整局 779 帧、33 个选项面板帧、32 个完整识别帧，点击 0，confirmed 为空。

游戏前台期间 Raw Input 收包恒为 4127，最后收包时间 1792413761057；左键计数恒为 29。游戏退出后收包恢复。这证明了旧实现的输入通道在游戏前台停收；日志不能进一步证明 Windows、驱动或游戏中的哪一层阻止了它，也不能凭选项出现就倒推出玩家已选。

没有已确认的“虚幻武器”进入 owned，联动栏按无候选隐藏；赵信的联动规划器本身能生成候选。因此应修复输入采集，不应让 UI 把看见的海克斯当作已选。

0.17 新增独立线程每 8ms 读取鼠标左键当前高位状态，在 Raw Input 超过 250ms 无收包时接管按下沿。忽略启动时已按住、保持和释放；两路复用去重门，恢复收包不会把一次点击记两次。仍检查游戏前台和客户区，仍需两帧面板消失确认，刷新操作与未知标题不入账。没有注入、拦截或发送游戏输入。

`input-health.jsonl` 与 `connection-status.json` 新增 buttonStatePolling、buttonStateChecks、buttonStateEdges、buttonStateClicks、lastButtonStatePollAtMs、lastInputSource、rawInputStalled。逐次点击日志保留 inputSource。

`tests/fixtures/xinzhao-spell-on-hit-panel.json` 只抽取真实卡片几何和标题。回归显式提供测试点击，验证生产确认、日志入账、赵信联动推荐和浮窗展开，绝不重写原局日志或声称恢复了原局实际选择。另以真实 WPF 测试轮询状态沿、双源去重、快速第二次点击和按住时启动。

Windows API 依据：[GetAsyncKeyState](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate)、[Raw Input overview](https://learn.microsoft.com/en-us/windows/win32/inputdev/about-raw-input)。状态采样使用高位；不使用不可靠的低位“曾按下”标记。不同桌面或权限隔离仍可能限制只读 API，须结合新日志定位，离线回归不等于实战输入送达验证。
