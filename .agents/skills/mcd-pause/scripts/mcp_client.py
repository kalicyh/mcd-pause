"""Small Streamable HTTP client. Credentials never enter generated views."""
import json
import os
import urllib.error
import urllib.request

ENDPOINT = "https://mcp.mcd.cn"
READ_TOOLS = frozenset({
    "now-time-info", "list-nutrition-foods", "campaign-calendar",
    "query-nearby-stores", "delivery-query-addresses", "delivery-query-stores",
    "query-meals", "query-meal-detail", "query-my-coupons", "query-store-coupons",
    "available-coupons", "calculate-price", "query-order", "order-list",
})


class McpError(ValueError):
    pass


def unwrap(response):
    """Accept recorded JSON-RPC results or a structured MCP business response."""
    if not isinstance(response, dict):
        raise McpError("MCP 响应必须是对象")
    if response.get("error"):
        raise McpError("MCP 协议返回错误；请检查工具名和参数")
    result = response.get("result", response)
    if not isinstance(result, dict):
        raise McpError("MCP 结果必须是对象")
    if result.get("isError"):
        raise McpError("MCP 工具执行失败；不会把失败结果当作数据")
    obj = result.get("structuredContent", result)
    if not isinstance(obj, dict):
        raise McpError("MCP 结构化结果必须是对象")
    if "data" not in obj and "content" in result:
        for block in result["content"]:
            if block.get("type") != "text":
                continue
            # Some clients preserve only the provider's JSON after this heading.
            text = block.get("text", "").split("## Original Response")[-1].strip()
            text = text.removeprefix("```json").removesuffix("```").strip()
            try:
                candidate = json.loads(text)
            except (ValueError, TypeError):
                continue
            if isinstance(candidate, dict) and "data" in candidate:
                obj = candidate
                break
    if obj.get("success") is False or ("code" in obj and obj["code"] != 200):
        raise McpError("MCP 业务请求未成功；检查登录、参数或门店状态")
    if "data" not in obj:
        raise McpError("无法识别 MCP 数据结构；需要核对原始响应")
    return obj["data"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise McpError("MCP 地址发生重定向，已停止以避免转发凭据")


class Client:
    def __init__(self, token=None):
        self.token = token or os.environ.get("MCD_MCP_TOKEN")
        if not self.token:
            raise McpError("未配置 MCD_MCP_TOKEN；也可使用宿主已连接的麦当劳 MCP")
        self.session = None
        self.protocol = "2025-03-26"
        self.counter = 0
        self.opener = urllib.request.build_opener(NoRedirect())
        initialized = self.rpc("initialize", {
            "protocolVersion": self.protocol, "capabilities": {},
            "clientInfo": {"name": "mcd-pause", "version": "0.1.0"},
        })
        self.protocol = initialized.get("protocolVersion", self.protocol)
        self.rpc("notifications/initialized", notification=True)

    def rpc(self, method, params=None, notification=False):
        self.counter += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notification:
            payload["id"] = self.counter
        headers = {"Authorization": "Bearer " + self.token,
                   "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": self.protocol}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(ENDPOINT, json.dumps(payload).encode(), headers)
        try:
            with self.opener.open(request, timeout=25) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise McpError("MCP 响应超过读取上限")
                body = raw.decode("utf-8-sig")
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise McpError(f"MCP HTTP {exc.code}；请求未自动重试") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise McpError("MCP 网络请求失败；请求未自动重试") from None
        if notification:
            return {}
        try:
            if "text/event-stream" in content_type or body.startswith("event:") or body.startswith("data:"):
                messages = []
                for event in body.replace("\r\n", "\n").split("\n\n"):
                    data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
                    if data:
                        messages.append(json.loads(data))
                obj = next(x for x in messages if x.get("id") == self.counter)
            else:
                obj = json.loads(body)
            if obj.get("id") != self.counter or obj.get("error"):
                raise McpError("MCP 协议响应错误；请核对当前工具定义")
            return obj["result"]
        except (ValueError, KeyError, StopIteration, AttributeError, TypeError) as exc:
            if isinstance(exc, McpError):
                raise
            raise McpError("MCP 返回了无法识别的响应") from None

    def call(self, name, arguments):
        if name not in READ_TOOLS:
            raise McpError("通用调用仅开放只读工具；创建订单必须走已核价的确认单")
        return self.rpc("tools/call", {"name": name, "arguments": arguments})

    def create_order(self, arguments):
        # Only the draft submission workflow calls this method. No retry here.
        return self.rpc("tools/call", {"name": "create-order", "arguments": arguments})
