# 选餐、确认与订单

## 找门店与商品

先采用用户本次提供的城市/位置、门店、取餐方式；否则复用明确保存的偏好。首次必要时询问。用户没给位置时不擅自选择演示门店。

| 场景 | beType | orderType | 门店来源与要求 |
|---|---:|---:|---|
| 到店自取 | 1 | 1 | `query-nearby-stores`；后续不传 beCode |
| 得来速 | 5 | 1 | `query-nearby-stores`；保留同条返回的 beCode |
| 麦乐送 | 2 | 2 | `delivery-query-addresses`、`delivery-query-stores`；确认用户选择的 addressId 与门店配对 |
| 企业团餐 | 6 | 2 | 对应配送门店；下单另需助餐服务码 |

查询附近门店时显式设置 `searchType=2`、`city`、`keyword`；`searchType=1` 查询的是收藏门店。创建新地址属于另外的明确操作，本脚本不自动创建。预约时将同一 `reservationDate` 贯穿菜单、详情、券、核价和下单。

调用 `query-meals` 后，只从当前返回的商品编码生成候选。自动组合首版覆盖一份主食、可选一份小食、可选一杯饮料；每类最多取参考价较低的 40 项。它不是全菜单全套餐的最优价格证明。价格为 0、随单购会员卡、复杂套餐不进入自动组合。

对选中商品调用 `query-meal-detail` 核对规格、套餐 roundList 和特调。需要选规格的通用商品不能把名称直接映射到“大杯/中份”等营养值；有明确规格后才建立完整名称别名。修改配料没有营养依据时对应营养维持未知。复杂套餐可以由 Agent 展开并手动组装 API 请求，但不能套用自动单品计算的总营养。

## 营养与优惠

`list-nutrition-foods` 实际 data 可能是 `[数量]{字段,...}:` 格式的表格文本。`nutrition --response` 支持该格式与 JSON 行列表。`catalog` 使用 NFKC 和空白归一化后的完整名称匹配，不删除份量、温度或括号，重复名称记为 ambiguous。额外别名只允许有规格证据的完整名称映射。

```sh
python3 "$SKILL_DIR/scripts/mcpause.py" catalog --menu work/menu.json --nutrition work/nutrition.json --context work/context.json --out work/catalog.json
python3 "$SKILL_DIR/scripts/mcpause.py" recommend --catalog work/catalog.json --budget 35 --max-kcal 650 --min-protein 30
```

上述目标只是命令示例，不是默认健康建议。按用户设定的目标计算。无法满足时解释缺口，给出调整选项；不要降低硬约束后仍宣称通过。游戏过关仅说明当前录入数据满足约束。

优惠先查用户已持有的券，再用 `query-store-coupons` 核对当前门店/方式。对原价与最多三组相关用券候选调用 `calculate-price`，展示实际返回的总价；不从活动标语推算券后价，不默认为可叠加。领取优惠券是写操作，只有用户另有要求时通过原生 MCP 执行；CLI 通用接口不开放领券。

## 一份确认单

1. 用 `calculate-price` 得到当前价格。2026-10-14 只读联调确认 `data.price` 为应付总额（整数分）；菜单 `currentPrice` 是元字符串。不要把所有工具的金额都除以 100。结构变化时停止并核对字段。
2. 到店/得来速从该返回的 `takeWayList[].code` 选取用户要求的方式。例如“堂食”和“外带”是不同选项，不默认互换。外送不传 takeWayCode，需 addressId。将最终 `create-order` 请求保存为私有临时 JSON。
3. 创建本地确认单：

```sh
python3 "$SKILL_DIR/scripts/mcpause.py" order draft --request work/order-request.json --response work/quote.json --amount-field price --budget 35 --summary '向用户展示的真实餐单、门店、取餐方式、配送信息摘要与总价'
```

4. 展示真实摘要、优惠/费用、本次总额、确认单编号和五分钟有效期。最后让用户明确确认；已有对这一份餐单的明确确认可直接进入下一步，不再问一次。返回的本地确认单编号不是麦当劳订单号。

## 提交：选择一种接入方式

### 宿主原生 MCP（推荐）

用户确认后，重新调用 `calculate-price`，参数必须来自已确认 request 的核价字段：storeCode、beType、orderType、beCode、items、reservationDate、gmServiceCode、needTableware、withOrder。不要把 create-order 独有的 addressId、takeWayCode、remark 传给核价。

```sh
python3 "$SKILL_DIR/scripts/mcpause.py" order authorize --draft CONFIRMATION_ID --confirm CONFIRMATION_ID --user-confirmed --response work/fresh-quote.json
```

只有返回 `authorized=true`，才使用其原样 `request` 调用一次原生 `create-order`。价格变化时返回 price_changed，展示新价，重新生成确认单。授权只是本地提交保护，不是绕过用户确认的依据。

将完整工具结果保存到 work/create-result.json，再记录：

```sh
python3 "$SKILL_DIR/scripts/mcpause.py" order complete --draft CONFIRMATION_ID --response work/create-result.json
```

若原生调用超时、被中断或没有结果，执行 `order uncertain --draft CONFIRMATION_ID`，停止提交并引导核对订单。原始调用已经可能到达服务端，不得换一个确认单再次尝试。

### 本地环境变量客户端

```sh
python3 "$SKILL_DIR/scripts/mcpause.py" order submit --draft CONFIRMATION_ID --confirm CONFIRMATION_ID --user-confirmed
```

此命令内部重新核价并提交一次。两个入口共享本地状态，不要对同一餐先后调用两种方式。相同餐单的并发提交也会被占用记录拦住。可执行本地代码的主体仍能绕过这些脚本，因此本工具不是独立的权限沙箱。

## 支付与订单

返回支付链接给用户本人操作。首版不会代付，也不会因用户说“支付好了”就标记成功。调用 `query-order` 后运行：

```sh
python3 "$SKILL_DIR/scripts/mcpause.py" order observe --order-id REAL_ORDER_ID --response work/order-result.json
# 或由环境变量客户端查询（同一单自动检查至少间隔三分钟）：
python3 "$SKILL_DIR/scripts/mcpause.py" order watch --order-id REAL_ORDER_ID
```

状态适配仅认识明确文字字段：orderStatusName/orderStatusDesc/statusName/statusDesc 等，以及 payStatusName/payStatusDesc/paymentStatus/payStatus。不猜数字状态码；未知状态展示原值并说明尚未映射。只显示真实返回的取餐码和预计时间，不计算虚构进度百分比。

开启庆祝后，首次明确“已支付”且“已接单/备餐中”才可执行 `order celebrate --order-id ...`。`fire=true` 时调用宿主 `fire_confetti` 一次。实际可识别的订单字段还需要用户真实订单联调；目前仅完成合成订单测试，不承诺每种状态都能自动识别。

持续跟进见 [定时任务](automations.md)。`changed=false` 时保持安静。订单终态、30 次成功检查、连续 3 次失败或两小时后停止对应临时任务。

提交结果不确定时，先在官方 App 或订单列表核对时间、餐单和金额。找到订单后 `order resolve --draft ... --order-id ...` 会查询并关联它；原生 MCP 路径可追加 `--response work/order-result.json`，导入带相同订单号的真实查询结果。明确确认没有创建该单后，才可用 `--confirmed-no-order` 解除本次占用。此命令不重新下单。
