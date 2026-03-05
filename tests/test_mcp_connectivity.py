#!/usr/bin/env python3
"""
MCP 远程连通性测试脚本 — open-deepwiki

测试 HTTP (streamable-http) 模式下的 MCP 服务连通性。
先启动服务：python -m app.mcp_server --transport http --port 8808

使用方式:
    python tests/test_mcp_connectivity.py
    python tests/test_mcp_connectivity.py --url http://localhost:8808
    python tests/test_mcp_connectivity.py --url http://localhost:8808 --token mytoken
    python tests/test_mcp_connectivity.py --url http://remote-host:8808 --token mytoken -v
"""

import argparse
import sys

# Windows GBK 终端无法直接输出 Unicode 符号，强制使用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import json
import sys
import time
from typing import Any

try:
    import requests
except ImportError:
    sys.exit("缺少依赖：pip install requests")

# ─── ANSI 颜色 ────────────────────────────────────────────────────────────────

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BLUE   = "\033[34m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def _ok(msg: str)   -> str: return f"{GREEN}✓{RESET} {msg}"
def _fail(msg: str) -> str: return f"{RED}✗{RESET} {msg}"
def _warn(msg: str) -> str: return f"{YELLOW}!{RESET} {msg}"


# ─── MCP streamable-http 最小客户端 ──────────────────────────────────────────

class MCPTestClient:
    """
    最小化 MCP streamable-http 客户端。

    协议要点：
    - 所有消息均 POST 到 /mcp
    - 请求头 Accept: application/json, text/event-stream
    - 服务器可返回 application/json（直接响应）或 text/event-stream（SSE 流）
    - 会话通过 mcp-session-id 响应头维持，后续请求须带回该头
    - 通知型消息（无 id 字段）服务器返回 202，无响应体
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, base_url: str, token: str | None = None, timeout: int = 15):
        self.endpoint = base_url.rstrip("/") + "/mcp"
        self.timeout  = timeout
        self.session_id: str | None = None
        self._req_id   = 0
        self._http            = requests.Session()
        self._http.trust_env  = False   # 禁止从 .netrc / 系统代理读取凭据，防止 Basic Auth 覆盖 Bearer Token
        self._http.headers.update({
            "Content-Type": "application/json",
            "Accept":        "application/json, text/event-stream",
        })
        if token:
            self._http.headers["Authorization"] = f"Bearer {token}"

    # ── 内部发送 ──────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _post(self, payload: dict) -> dict:
        """发送 JSON-RPC 消息，自动处理 SSE/JSON 双格式响应。"""
        if self.session_id:
            self._http.headers["mcp-session-id"] = self.session_id

        resp = self._http.post(
            self.endpoint,
            json=payload,
            timeout=self.timeout,
            stream=True,
        )

        # 服务器初始化时下发 session id
        if sid := resp.headers.get("mcp-session-id"):
            self.session_id = sid

        # 202 = 通知已接受，无响应体
        if resp.status_code == 202:
            return {}

        if resp.status_code == 401:
            raise PermissionError("认证失败（401）：请检查 --token 是否正确")

        if resp.status_code != 200:
            raise ConnectionError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            return self._parse_sse(resp)
        return resp.json()

    @staticmethod
    def _parse_sse(resp: requests.Response) -> dict:
        """
        按 SSE 规范解析响应流，正确处理多行 data: 事件和 UTF-8 编码。

        使用 resp.content 一次性读取完整响应体，避免 iter_lines 在 UTF-8
        多字节字符的 chunk 边界上截断行（MCP 工具描述含中文时会触发此问题）。
        """
        raw = resp.content.decode("utf-8", errors="replace")
        data_lines: list[str] = []
        for line in raw.splitlines():
            if line == "":
                # 空行 = SSE 事件结束，尝试解析已收集的 data
                if data_lines:
                    data_str = "\n".join(data_lines)
                    if data_str and data_str != "[DONE]":
                        return json.loads(data_str)
                    data_lines = []
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
            # 忽略 event:, id:, retry: 等其他字段
        # 流结束时处理未被空行终止的最后一条事件
        if data_lines:
            data_str = "\n".join(data_lines)
            if data_str and data_str != "[DONE]":
                return json.loads(data_str)
        return {}

    # ── 公共方法 ──────────────────────────────────────────────────────────────

    def initialize(self) -> dict:
        """MCP 握手，必须首先调用。返回完整的 initialize 响应。"""
        result = self._post({
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  "initialize",
            "params": {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities":    {},
                "clientInfo":      {"name": "mcp-connectivity-tester", "version": "1.0"},
            },
        })
        # 发送 initialized 通知（无 id，服务器返回 202）
        self._post({
            "jsonrpc": "2.0",
            "method":  "notifications/initialized",
        })
        return result

    def list_tools(self) -> list[dict]:
        resp = self._post({
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  "tools/list",
        })
        return resp.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        """
        调用 MCP 工具，自动解析 content 数组中的 text 字段。
        若 text 是 JSON 字符串则反序列化后返回，否则直接返回字符串。
        """
        resp = self._post({
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  "tools/call",
            "params":  {"name": name, "arguments": arguments or {}},
        })
        if "error" in resp:
            raise RuntimeError(f"工具错误: {resp['error']}")

        content = resp.get("result", {}).get("content", [])
        texts = [c["text"] for c in content if c.get("type") == "text"]
        if not texts:
            return None
        # FastMCP 对 list[dict] 返回值的序列化方式：每个元素单独作为一个 content 项。
        # 多条 text 且每条都是 JSON 对象 → 重新拼为列表。
        if len(texts) > 1:
            parsed = []
            for t in texts:
                t = t.strip()
                if t.startswith(("{", "[")):
                    parsed.append(json.loads(t))
                else:
                    parsed.append(t)
            return parsed
        raw = texts[0]
        if raw.startswith(("{", "[")):
            return json.loads(raw)
        return raw


# ─── 测试套件 ─────────────────────────────────────────────────────────────────

# 预期工具集（Module 6 定义的 8 个工具）
EXPECTED_TOOLS = {
    "list_repositories",
    "search_codebase",
    "get_code_chunks",
    "read_file",
    "get_repository_overview",
    "get_wiki_content",
    "get_dependency_graph",
    "list_files",
}


def run_tests(client: MCPTestClient, verbose: bool) -> int:
    """
    依次运行 5 项测试，返回失败数量（0 = 全部通过）。

    测试项：
      1. MCP 握手（initialize）
      2. 工具列表完整性（tools/list）
      3. list_repositories
      4. get_repository_overview（若有仓库）
      5. list_files（若有仓库）
    """
    records: list[tuple[str, bool, str]] = []
    failures = 0

    def record(name: str, passed: bool, detail: str = "") -> None:
        nonlocal failures
        records.append((name, passed, detail))
        if not passed:
            failures += 1

    # ── 1. 握手 ───────────────────────────────────────────────────────────────
    print(f"\n{BOLD}[1/5] MCP 握手 (initialize){RESET}")
    try:
        t0   = time.monotonic()
        init = client.initialize()
        ms   = (time.monotonic() - t0) * 1000

        res       = init.get("result", {})
        srv       = res.get("serverInfo", {})
        proto     = res.get("protocolVersion", "?")
        srv_name  = srv.get("name", "?")
        srv_ver   = srv.get("version", "?")
        passed    = bool(res)

        record("MCP 握手", passed,
               f"server={srv_name} v{srv_ver}, protocol={proto}, {ms:.0f}ms")
        status = _ok(f"server={srv_name} v{srv_ver}  protocol={proto}") if passed \
                 else _fail("initialize 响应为空")
        print(f"  {status}  ({ms:.0f} ms)")
        if client.session_id and verbose:
            print(f"  {BLUE}·{RESET} session-id = {client.session_id}")

    except PermissionError as e:
        record("MCP 握手", False, str(e))
        print(f"  {_fail(str(e))}")
        _print_summary(records)
        return 1
    except Exception as e:
        record("MCP 握手", False, str(e))
        print(f"  {_fail(str(e))}")
        print(f"\n{RED}无法建立 MCP 连接，终止后续测试。{RESET}")
        _print_summary(records)
        return 1

    # ── 2. 工具列表 ────────────────────────────────────────────────────────────
    print(f"\n{BOLD}[2/5] 工具列表 (tools/list){RESET}")
    tools: list[dict] = []
    try:
        tools      = client.list_tools()
        tool_names = {t["name"] for t in tools}
        missing    = EXPECTED_TOOLS - tool_names
        extra      = tool_names - EXPECTED_TOOLS
        passed     = not missing

        record("工具列表完整", passed,
               f"共 {len(tool_names)} 个，缺失={missing or '无'}")
        print(f"  {_ok(f'共 {len(tool_names)} 个工具，全部就绪') if passed else _fail(f'缺失工具: {missing}')}")
        if verbose:
            for t in sorted(tools, key=lambda x: x["name"]):
                print(f"    {BLUE}·{RESET} {t['name']}")
        if extra:
            print(f"  {_warn(f'未在预期列表中的额外工具: {extra}')}")

    except Exception as e:
        record("工具列表完整", False, str(e))
        print(f"  {_fail(str(e))}")

    # ── 3. list_repositories ──────────────────────────────────────────────────
    print(f"\n{BOLD}[3/5] list_repositories{RESET}")
    repos: list[dict] = []
    try:
        t0    = time.monotonic()
        repos = client.call_tool("list_repositories") or []
        ms    = (time.monotonic() - t0) * 1000

        if repos and isinstance(repos[0], dict) and "error" in repos[0]:
            record("list_repositories", False, repos[0]["error"])
            print(f"  {_fail(repos[0]['error'])}")
            repos = []
        else:
            record("list_repositories", True, f"{len(repos)} 个就绪仓库，{ms:.0f} ms")
            print(f"  {_ok(f'{len(repos)} 个就绪仓库')}  ({ms:.0f} ms)")
            if verbose and repos:
                for r in repos[:5]:
                    rid  = str(r.get("repo_id", "?"))[:8]
                    name = r.get("name", "?")
                    url  = r.get("url", "?")
                    print(f"    {BLUE}·{RESET} [{rid}] {name} — {url}")
                if len(repos) > 5:
                    print(f"    ... 还有 {len(repos) - 5} 个")
            if not repos:
                print(f"  {_warn('暂无就绪仓库，后两项测试将跳过')}")

    except Exception as e:
        record("list_repositories", False, str(e))
        print(f"  {_fail(str(e))}")
        repos = []   # 重置，防止非列表值流入后续测试

    # ── 4. get_repository_overview ────────────────────────────────────────────
    print(f"\n{BOLD}[4/5] get_repository_overview{RESET}")
    if not repos:
        print(f"  {_warn('无可用仓库，跳过')}")
        record("get_repository_overview", True, "跳过（无就绪仓库）")
    else:
        repo   = repos[0]
        rid    = repo.get("repo_id", "")
        rname  = repo.get("name", rid)
        try:
            t0       = time.monotonic()
            overview = client.call_tool("get_repository_overview", {"repo_id": rid})
            ms       = (time.monotonic() - t0) * 1000

            if isinstance(overview, dict) and "error" in overview:
                record("get_repository_overview", False, overview["error"])
                print(f"  {_fail(overview['error'])}")
            else:
                sections = overview.get("total_sections", 0)
                pages    = overview.get("total_pages", 0)
                record("get_repository_overview", True,
                       f"{rname}: {sections} sections, {pages} pages, {ms:.0f} ms")
                print(f"  {_ok(f'{rname}: {sections} sections, {pages} pages')}  ({ms:.0f} ms)")

        except Exception as e:
            record("get_repository_overview", False, str(e))
            print(f"  {_fail(str(e))}")

    # ── 5. list_files ─────────────────────────────────────────────────────────
    print(f"\n{BOLD}[5/5] list_files{RESET}")
    if not repos:
        print(f"  {_warn('无可用仓库，跳过')}")
        record("list_files", True, "跳过（无就绪仓库）")
    else:
        rid = repos[0].get("repo_id", "")
        try:
            t0    = time.monotonic()
            files = client.call_tool("list_files", {
                "repo_id":    rid,
                "extensions": [".py"],
            })
            ms    = (time.monotonic() - t0) * 1000

            if files and isinstance(files[0], dict) and "error" in files[0]:
                record("list_files", False, files[0]["error"])
                print(f"  {_fail(files[0]['error'])}")
            else:
                count = len(files) if isinstance(files, list) else 0
                record("list_files", True, f"{count} 个 .py 文件，{ms:.0f} ms")
                print(f"  {_ok(f'{count} 个 .py 文件')}  ({ms:.0f} ms)")
                if verbose and files:
                    for f in files[:5]:
                        print(f"    {BLUE}·{RESET} {f}")
                    if count > 5:
                        print(f"    ... 还有 {count - 5} 个")

        except Exception as e:
            record("list_files", False, str(e))
            print(f"  {_fail(str(e))}")

    # ── 汇总 ──────────────────────────────────────────────────────────────────
    _print_summary(records)
    return failures


def _print_summary(records: list[tuple[str, bool, str]]) -> None:
    total  = len(records)
    passed = sum(1 for _, ok, _ in records if ok)
    failed = total - passed

    print(f"{'─' * 52}")
    pass_str = f"{GREEN}{passed} 通过{RESET}"
    fail_str = f"{RED}{failed} 失败{RESET}" if failed else ""
    print(f"{BOLD}测试汇总{RESET}  {pass_str}{'  ' + fail_str if fail_str else ''}")
    for name, ok_, detail in records:
        status = f"{GREEN}PASS{RESET}" if ok_ else f"{RED}FAIL{RESET}"
        suffix = f"  — {detail}" if detail else ""
        print(f"  [{status}] {name}{suffix}")
    print()


# ─── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="测试 open-deepwiki MCP 远程连通性（streamable-http 模式）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 本地默认端口，无鉴权
  python tests/test_mcp_connectivity.py

  # 指定远程地址
  python tests/test_mcp_connectivity.py --url http://192.168.1.10:8808

  # 带 Bearer Token 鉴权 + 详细输出
  python tests/test_mcp_connectivity.py --url http://localhost:8808 --token mytoken -v
        """,
    )
    parser.add_argument(
        "--url", default="http://localhost:8808",
        help="MCP 服务地址（默认: http://localhost:8808）",
    )
    parser.add_argument(
        "--token", default=None,
        help="Bearer Token（对应服务端 MCP_AUTH_TOKEN，未配置则留空）",
    )
    parser.add_argument(
        "--timeout", type=int, default=15,
        help="每次请求超时秒数（默认: 15）",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="显示工具列表、仓库列表、文件列表等详细信息",
    )
    args = parser.parse_args()

    print(f"\n{BOLD}open-deepwiki MCP 连通性测试{RESET}")
    print(f"  端点: {BLUE}{args.url}/mcp{RESET}")
    print(f"  认证: {'Bearer Token 已配置' if args.token else '无（MCP_AUTH_TOKEN 未设置）'}")
    print(f"  超时: {args.timeout}s")

    client   = MCPTestClient(args.url, args.token, args.timeout)
    failures = run_tests(client, args.verbose)
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
