# 麦麦暂停键 · McPause

**在 Codex 里暂停一下，安排一餐。**

McPause 是麦当劳中国 MCP 的用餐 Skill。它把饭点提醒、预算与营养搭配、聊天提醒、浏览器图文选餐、确认下单和订单跟进连接起来。

目标用户是容易错过饭点的开发者，以及希望按自己设定的预算和营养目标选餐的人。独立开发，非麦当劳官方产品。

## 首版可以做什么

| 场景 | 操作 |
|---|---|
| 到饭点 | 用户启用后，由 Codex 定时唤醒；已吃、延后、免打扰和每日去重 |
| 选这一餐 | 确认吃饭后打开浏览器；想吃的餐品置顶，后面是完整官方分类菜单和图片；独立详情页调整套餐、饮料、冰量及配料，直接查询实时价格 |
| 营养搭配 | 查询门店、菜单、营养和已持有优惠；按预算、能量、蛋白与可选钠上限筛选 |
| 开卡划算吗 | 比较同一餐单不开卡与加购卡的卡费、配送/打包减免和实付；显示扣卡费后的净省，并按可调整的后续节省假设估算回本次数 |
| 修复一餐 | 在聊天界面里调整搭配，寻找一次同类替换，观察营养与参考价格变化 |
| 确认下单 | 展示具体餐单与金额；五分钟确认单、提交前重新核价、重复提交保护 |
| 看订单 | 进入订单页后自动每三分钟检查进行中的订单，完成或取消后停止；用户开启庆祝后，确认外送送达时播放一次礼花 |

用户输入“麦门永存”“去码头整点薯条”“给身体打个蛋白补丁”“我的补给到哪了”可以进入对应场景。讨论梗的含义不会触发点餐；没有后台麦克风监听。

## 快速体验

需要 Python 3.10+，运行脚本只用标准库。无需安装前端依赖、启动业务服务或提供 Token 即可体验演示：

```sh
python3 .agents/skills/mcd-pause/scripts/mcpause.py demo --inline --out work/demo.html
```

在 Codex 中让 Skill 按宿主 visualize 指南呈现生成的片段；仅运行命令不会自动显示界面。其中商品、价格、营养和订单均为**虚构示例**。演示不联网、不下单、不创建提醒，无需打开浏览器。

## 安装

跟你的 AI 说：

> 安装 [https://github.com/kalicyh/mcd-pause](https://github.com/kalicyh/mcd-pause)

技能位于 `.agents/skills/mcd-pause`。安装后，说“我要吃饭”或“麦门永存”即可开始。

## 在 Codex 中使用

项目只提供 Skill 和辅助脚本，复用已连接的麦当劳 MCP 与宿主 visualize 能力；无需自行部署或注册界面服务。日常使用不改全局配置、不更新已安装的技能、不安装运行环境或要求重启。聊天内提供饭点提醒、偏好快选、真实菜单配餐小游戏和订单操作入口；确认吃饭后启动 Skill 的临时本机点餐页，核价和进度直接在页面更新。完整流程见 [界面说明](.agents/skills/mcd-pause/references/interface.md)。

麦当劳业务连接方式二选一：

1. **宿主 MCP 连接**：按[官方指南](https://github.com/M-China/mcd-mcp-server)连接 `https://mcp.mcd.cn`，由宿主管理凭据。Skill 优先使用此连接。
2. **本地客户端**：通过本机环境变量 `MCD_MCP_TOKEN` 提供凭据；临时检查可不回显输入、不保存：

```sh
python3 .agents/skills/mcd-pause/scripts/mcpause.py --token-stdin doctor
```

`doctor` 只列工具并查询服务器时间和营养表，不读取个人订单、地址或券包，也不消费。

可直接对 Codex 说：

> 使用 $mcd-pause。帮我找一份预算 35 元、蛋白质至少 30 克的午餐。

> 开启工作日午餐提醒，按我的时区 12:00 吃饭，提前 20 分钟提醒；已经安排好时不要重复提醒。

> 这顿编译通过了吗？我想保留汉堡，看看换掉小食能减少多少热量。

> 这份餐开卡划算吗？算上卡费能省多少，吃几次回本？先比较，不开卡。

## 数据与行为边界

- 营养目标由用户设定。菜单参考价不是最终实付价；最终价格来自 `calculate-price`。
- 营养表没有统一商品 ID，使用完整名称严格匹配；大小份、温度不明、同名歧义和未收录商品均保留未知。未知值不能按 0 计算。
- 营养自动推荐覆盖单品组合；套餐推荐使用真实菜单和详情，新品标记来自明确菜单标签或当前官方活动。推荐与详情分为两个页面，点击套餐进入改配，返回时保留选择。界面支持单选和多选多份轮次，冰量、配料 key 与价差来自详情；复杂搭配预估价未知时直接官方核价。不声称覆盖全部优惠或找到全局最低价。
- `SKILL.md` 不是后台进程。提醒需要宿主定时任务，聊天礼炮需要宿主效果工具，浏览器礼花由页面播放；聊天提醒需要宿主界面能力。缺失能力时保留文本流程。
- 餐品数据保留菜单接口返回的官方图片 URL，不下载或打包到仓库。当前聊天内 visualize 的资源名单不包含官方图片域，因此仅显示文字选餐与改配，Skill 不能扩大宿主权限。确认用餐后，浏览器是图文选餐入口；详情未提供对应图片时，不用其他规格图片代替。
- 用户确认的是具体餐单；支付由本人完成。创建订单超时会停下来核对，不自动重试。
- 订单字段按官方 query-order schema 和明确语义文本识别，其他状态原样展示；当前无骑手坐标或轨迹接口，不展示假地图。没有预计完成时间就不画倒计时。
- 不读取其他聊天来推断“还在写代码”。首版使用用户启用的饭点模式和当前工作上下文。
- 开卡对比复用 `query-meals.withOrder` 与 `calculate-price`。卡费须来自真实核价明细，已包含在应付总额时不重复相加；不按划线价计算净省。首单免配送或打包不代表以后每单都免，回本次数是明确假设下的估算，受卡有效期、门槛及次数限制。比较不会实际开卡。

## 验证

```sh
python3 -m unittest discover -s tests -v
node tests/test_membership_ui.cjs
node tests/test_checkout_ui.cjs
node tests/test_browser_ui.cjs
node tests/test_window_ui.cjs
python3 .agents/skills/mcd-pause/scripts/mcpause.py demo --inline --out work/demo.html
```

自动测试使用合成数据和临时状态目录，不连接真实服务、不下单。只读联调情况、未验证部分见 [MCP_INTEGRATION.md](MCP_INTEGRATION.md)。Token、订单状态与用户偏好不进入 Git；默认状态目录为 `~/.local/share/mcd-pause`，可用 `MCPAUSE_STATE_DIR` 隔离。

当前已通过 68 项 Python 离线测试和四组 Node 界面检查，覆盖自动比较、旧报价丢弃、卡方案绑定、同总额卡费变化、明确确认后一次提交，以及真实分类保留、本机来源校验、自动订单检查、状态切换、送达庆祝去重和聊天操作回传。使用合成数据的提交测试不会创建真实订单。

浏览器实测已确认官方图片、自动核价与开卡对比、可展开明细，以及不开卡/随单开卡两种本地确认单；未提交真实订单。菜单快照含 14 个分类、122 个餐品。默认不开卡，最终确认后才创建订单并展示官方支付链接；支付由用户完成。真实创建订单、付款及送达礼花仍未进行实单验证。Skill 结构校验若缺少 PyYAML，不为此安装依赖或修改全局 Python。

## 原创性与相近项目

本项目根据本次需求独立编写，未复制其他参赛项目的实现；不宣称饭点提醒、营养推荐、游戏化点餐或彩纸效果是首次出现。

已有相关作品包括 [mcd-cooldown](https://github.com/Adonis0123/mcd-cooldown) 的编程饭点提醒、[McPet](https://github.com/songconmaisaix31-design/mcpet) 的桌面伴侣、[McFit Coach](https://github.com/dickeryang/mcd-fit-coach) 的营养报告、[mcd-gacha](https://github.com/ZDF-CC/mcd-gacha) 的互动选餐。McPause 聚焦 Codex 中的用餐安排，并实现有约束检查与一次替换建议的配餐互动。

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
