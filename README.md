# 麦麦暂停键 · McPause

**在 Codex 里暂停工作，安排一餐，再接回思路。**

McPause 是麦当劳中国 MCP 的用餐 Skill。它把饭点提醒、预算与营养搭配、交互选餐、确认下单和订单跟进放进同一段聊天，并用一张三行续写卡保存吃饭前的工作思路。

目标用户是容易错过饭点的开发者，以及希望按自己设定的预算和营养目标选餐的人。独立开发，非麦当劳官方产品。

## 首版可以做什么

| 场景 | 操作 |
|---|---|
| 准备吃饭 | 保存“正在解决什么／已验证什么／回来第一步”的续写卡 |
| 到饭点 | 用户启用后，由 Codex 定时唤醒；已吃、延后、免打扰和每日去重 |
| 选这一餐 | 查询门店、菜单、营养和已持有优惠；按预算、能量、蛋白与可选钠上限筛选 |
| 修复一餐 | 在聊天界面里调整搭配，寻找一次同类替换，观察营养与参考价格变化 |
| 确认下单 | 展示具体餐单与金额；五分钟确认单、提交前重新核价、重复提交保护 |
| 看订单 | 查询真实状态；用户开启庆祝后，在明确支付且接单时调用宿主礼炮一次 |
| 吃完回来 | 读取续写卡，核对工作现场，再继续下一步 |

用户输入“麦门永存”“去码头整点薯条”“给身体打个蛋白补丁”“我的补给到哪了”可以进入对应场景。讨论梗的含义不会触发点餐；没有后台麦克风监听。

## 快速体验

需要 Python 3.10+，运行脚本只用标准库。无需安装前端依赖、启动业务服务或提供 Token 即可体验演示：

```sh
python3 .agents/skills/mcd-pause/scripts/mcpause.py demo --out work/demo.html
```

用浏览器打开 `work/demo.html`。其中商品、价格、营养、工作进度和订单均为**虚构示例**。演示不联网、不下单、不创建提醒；普通浏览器也不能替代 Codex 的宿主能力。

## 在 Codex 中使用

仓库技能位于 `.agents/skills/mcd-pause`。在该仓库打开 Codex 后，可显式使用 `$mcd-pause`。希望在其他项目使用时，复制整个技能目录到宿主的用户技能目录，或运行安装助手：

```sh
python3 scripts/install_skill.py --dest ~/.codex/skills
```

安装器只复制技能，不改 MCP 配置、不写入凭据，也不会自动开启饭点任务。目标同名技能存在且内容不同时会停止；选择明确的备份更新参数后才替换。

连接方式二选一：

1. **宿主 MCP 连接**：按[官方指南](https://github.com/M-China/mcd-mcp-server)连接 `https://mcp.mcd.cn`，由宿主管理凭据。Skill 优先使用此连接。
2. **本地客户端**：通过本机环境变量 `MCD_MCP_TOKEN` 提供凭据；临时检查可不回显输入、不保存：

```sh
python3 .agents/skills/mcd-pause/scripts/mcpause.py --token-stdin doctor
```

`doctor` 只列工具并查询服务器时间和营养表，不读取个人订单、地址或券包，也不消费。

可直接对 Codex 说：

> 使用 $mcd-pause。先记下我现在工作的进度，再帮我找一份预算 35 元、蛋白质至少 30 克的午餐。

> 开启工作日午餐提醒，按我的时区 12:00 吃饭，提前 20 分钟提醒；已经安排好时不要重复提醒。

> 这顿编译通过了吗？我想保留汉堡，看看换掉小食能减少多少热量。

> 继续开工，接回刚才的续写卡。

## 数据与行为边界

- 营养目标由用户设定。菜单参考价不是最终实付价；最终价格来自 `calculate-price`。
- 营养表没有统一商品 ID，使用完整名称严格匹配；大小份、温度不明、同名歧义和未收录商品均保留未知。未知值不能按 0 计算。
- 自动推荐覆盖单品组合。复杂套餐、特调需要查询详情后由 Agent 处理，不声称覆盖全部优惠或找到全局最低价。
- `SKILL.md` 不是后台进程。提醒需要宿主定时任务，礼炮需要宿主效果工具，聊天交互需要宿主界面能力。缺失能力时保留文本流程。
- 用户确认的是具体餐单；支付由本人完成。创建订单超时会停下来核对，不自动重试。
- 订单字段只识别有明确语义的文本，未知数字状态原样展示。没有预计完成时间就不画倒计时。
- 不读取其他聊天来推断“还在写代码”。首版使用用户启用的饭点模式和当前工作上下文。

## 验证

```sh
python3 -m unittest discover -s tests -v
python3 .agents/skills/mcd-pause/scripts/mcpause.py demo --out work/demo.html
```

自动测试使用合成数据和临时状态目录，不连接真实服务、不下单。只读联调情况、未验证部分见 [MCP_INTEGRATION.md](MCP_INTEGRATION.md)。Token、订单状态、用户偏好与工作摘要不进入 Git；默认状态目录为 `~/.local/share/mcd-pause`，可用 `MCPAUSE_STATE_DIR` 隔离。

当前已通过 37 项离线测试及 Skill 结构检查。真实下单、线上订单字段、原生礼炮与定时调度仍待实际场景验证；本机浏览器预览连接受阻，界面交互验收未完成。

## 原创性与相近项目

本项目根据本次需求独立编写，未复制其他参赛项目的实现；不宣称饭点提醒、营养推荐、游戏化点餐或彩纸效果是首次出现。

已有相关作品包括 [mcd-cooldown](https://github.com/Adonis0123/mcd-cooldown) 的编程饭点提醒、[McPet](https://github.com/songconmaisaix31-design/mcpet) 的桌面伴侣、[McFit Coach](https://github.com/dickeryang/mcd-fit-coach) 的营养报告、[mcd-gacha](https://github.com/ZDF-CC/mcd-gacha) 的互动选餐。McPause 聚焦 Codex 聊天内的“暂停—用餐—接回工作”，并实现有约束检查与一次替换建议的配餐互动。

“麦门永存”“去码头整点薯条”是已有网络用语，参考 [Bilibili 示例](https://www.bilibili.com/video/BV1tm411978Q/)和[梗介绍](https://www.bilibili.com/video/BV1RF411q7gk/)，未复制视频、音频或图片素材。

## 参赛材料

按[官方详细活动规则](https://github.com/M-China/mcd-developer-innovation-challenge/blob/main/activityGuidelines.md)整理以下材料；详细规则另要求脱敏配置示例，不能只参考首页文件表：

| 文件 | 内容 |
|---|---|
| `README.md` | 项目介绍、安装方法、使用示例和目标用户 |
| `CONTEST_DECLARATION.md` | 从官方仓库原样取得，文件名与内容保持不变 |
| `MCP_INTEGRATION.md` | MCP 服务、工具、流程、价值与实际验证情况 |
| `mcp-config.example.json` | 仅含环境变量占位符的脱敏接入示例 |
| `.agents/skills/mcd-pause/`、`scripts/`、`tests/` | 技能、可运行源码、安装助手与离线验证 |

项目使用 Codex 开发，不参加要求真实 WorkBuddy 开发记录的专项奖励，不提供虚构的 `workbuddy.md`。

正式报名还需将项目发布到个人 GitHub 账号下的公开仓库，并按[官方报名格式](https://github.com/M-China/mcd-developer-innovation-challenge#通过-issue-报名)提交 Issue。各字段名称与内容放在同一行，项目地址使用完整纯文本 URL。当前尚未提交报名 Issue；材料齐备与本地 Git 提交不代表已经报名成功。
