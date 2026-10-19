# 2026-10-11 已选海克斯记录诊断

## 实战证据

最新有完整识别记录的对局为 `20261011-104945-1`，英雄齐天大圣。后一个目录 `20261011-104945-2` 是对局结束后的序号，检查时为空，不能当作新的实战。

- 1154 次截图；105 次检测到选项，其中 58 次三卡完整识别、47 次部分识别。
- `summary.json`：`clicks=0`、`confirmed=[]`、`unresolved=0`。
- 没有 `input.jsonl`；有真实游戏前台截图、HWND 393674、2560×1440 客户区和三卡点击几何。
- 运行前端为 0.12，排除上次旧 EXE 的问题。最终连接快照：旧鼠标钩子接收 152 次左键，但游戏前台及客户区次数均为 0。

断点位于 Windows 点击采集/前台过滤到 C# 卡片事件之间。Python 未收到点击，因此没有 pending，也没有机会确认。不能从候选标题反推用户最终选择；历史 `confirmed` 保留原值。

现有快照只给出累计计数，缺少逐局输入历史，无法确定是钩子失活、事件未送达还是前台过滤。`clickListenerInstalled=true` 只检查旧钩子句柄非零，不能证明钩子继续工作。微软说明低级鼠标钩子可能因超时被静默移除且应用无法检测，并建议此类后台观察使用 Raw Input。它是合理嫌疑，尚非已证实的实战根因。

## 0.13 修复与验证

- 独立消息窗口和 STA 线程注册鼠标 `RIDEV_INPUTSINK`，通过 `WM_INPUT` 获取物理左键按下；不设置 NOLEGACY，不拦截游戏输入。
- 全局物理鼠标坐标与游戏客户区匹配；前台子窗口归一到根窗口；OCR 返回 HWND 时立即同步监听目标。
- 每局 `input-health.jsonl` 每 5 秒记录收包、左键、前台、客户区计数和确认状态。`input.jsonl` 增加几何缺失、过期、窗口不符、卡片外、已有待确认选择等判定。
- 区分 `backendVersion` 与 C# `connection-status.json.version`，不再用共享 Python 文件版本证明前端版本。
- 生产后端回放真实三卡几何，补入中卡点击后，两帧面板消失会确认“魔法转物理”，写入测试临时目录的 `summary.confirmed`。它证明事件送达后的链路可用，不证明旧局该卡实际被选择。
- 111 个 Python 回归通过；C# 场景测试通过，并实际验证本机 Raw Input 注册成功与左键/释放/右键/滚轮/截断数据包处理。
- 在用户实际桌面运行独立监听探针 20 秒：收到 1293 个真实鼠标数据包、11 次左键，监听无报错；未发送模拟输入。证明桌面物理输入送达，尚未验证游戏前台过滤和卡片选择。
- 已正常关闭旧助手，默认 EXE 替换为 0.13 并重新启动；默认与版本 EXE SHA-256 一致：`F4C572B9824230424B679C1A0EDEF0B2965319F64F9A68EC5CF74EEBE157207B`。

新监听方式仍须一次实战输入确认，不能把注册或回放测试称作实战通过。

## ARAMGG 条件推荐审计

已在线核对官方 HEAD，本地源码与最新提交一致：`e07cef86e7f88a16aa6046eabfc4cc7c4ab8da5a`。

1. 局内 OCR 读取当前左、中、右候选标题；`auto-screenshot-service.ts::_loadAugmentWinratePayload` 按英雄取得单项统计，再按候选 ID 筛选和补齐卡位。
2. `modules/ipc-handlers.ts::get-winrate` 的有效推荐输入是 `championId`、`augmentIds`；没有已选集合。`data-loader.ts::getChampionAugmentStats` 读取 `detail.augments` 并按单项排名排序。没有查询“已选 A、B 后候选 C”的条件胜率。
3. `data-loader.ts::loadChampionAugmentTrios`、`mapPublicAugmentTrio` 确实支持加载三项组合的胜场、场数和胜率，并传给英雄详情；该链路没有接入局内已选记录或候选比较。不能把它的存在当作完整条件推荐实现。
4. `services/post-game-share.ts::buildSnapshotUpdateFromPayload` 会定位本人，递归寻找接口 payload 中 augment/upgrade/hex 相关字段的已知 ID，合并到战后分享快照。此方法依赖接口确实提供字段；它不依赖点击钩子，也不把快照作为局内条件胜率查询参数。

本机当前 16.19.1 数据包含 173 位英雄：138 位有三项组合，合计 623 行，每英雄最多 5 行；场数中位数 180，最大 2013；584 行来自 16.18、39 行来自 16.17。齐天大圣仅 1 行，151 场、16.17。它们是稀疏的组合统计，并非全部组合、全部选择轮次的联合分布。

所需量是 `P(胜利 | 英雄, 已选集合, 候选)`。单项胜率不能计算这个量；稀疏三项组合可以支持特定完整组合的历史参考，但没有覆盖大部分已选上下文，也不提供轮次顺序或完整分母，不能对缺失组合做简单加权冒充条件胜率。当前 hexgod 生产 `AramggEngine.rank_choices` 明确返回 `conditionalStatus=unavailable`，按单项排名推荐；记入已选后仅部分装备机制提示随之变化。

因此存在两个独立问题：我们的自建点击记录链路此次实战失败；完整条件胜率推荐在 aramgg 客户端与当前 hexgod 生产链路均未实现。直接复制它的单项推荐代码不会解决二者。

参考源码：[局内补齐](https://github.com/valkia/aramgg_client/blob/e07cef86e7f88a16aa6046eabfc4cc7c4ab8da5a/src/main/auto-screenshot-service.ts#L1233)、[胜率查询](https://github.com/valkia/aramgg_client/blob/e07cef86e7f88a16aa6046eabfc4cc7c4ab8da5a/src/main/modules/ipc-handlers.ts#L893)、[三项组合加载](https://github.com/valkia/aramgg_client/blob/e07cef86e7f88a16aa6046eabfc4cc7c4ab8da5a/src/main/data-loader.ts#L2484)、[战后快照](https://github.com/valkia/aramgg_client/blob/e07cef86e7f88a16aa6046eabfc4cc7c4ab8da5a/src/main/services/post-game-share.ts#L906)。

Windows 文档：[低级鼠标钩子及超时](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelmouseproc)、[Raw Input 后台接收](https://learn.microsoft.com/en-us/windows/win32/inputdev/about-raw-input)。
