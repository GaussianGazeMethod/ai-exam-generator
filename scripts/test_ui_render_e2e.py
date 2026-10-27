#!/usr/bin/env python3
"""UI 渲染端到端校验 — 用无头 Chrome 真实渲染每个角色/路由，并断言关键 DOM。

为什么需要它（对应 spec/开发自测清单.md 第 8.7 条「浏览器实际走通一遍」）：
    tsc 与构建只能证明「代码能编译」，证明不了「页面打开后真的有数据」。
    本项目曾出现过「审核工作台整页打不开」「题库页无数据仍显示假数据」这类
    只有真实浏览器才能发现的问题，因此把浏览器校验固化成脚本。

用法：
    python3 scripts/test_ui_render_e2e.py              # 校验全部用例
    python3 scripts/test_ui_render_e2e.py --shots DIR  # 同时把截图存到 DIR
    python3 scripts/test_ui_render_e2e.py --keep-chrome

依赖：后端 8000、Web 5173、移动端 5174（python3 scripts/services.py start）
     与系统安装的 Google Chrome（无第三方 Python 依赖）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = "http://localhost:8000"
WEB = "http://localhost:5173"
CDP_PORT = 9333
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE = "/tmp/dsh-chrome-ui-check"

# ── 用例：角色 / 路由 / DOM 断言 ────────────────────────────────────────
# ("text", 子串)：页面可见文本必须包含
# ("min", 选择器简写, 数量)：元素数量下限
CASES: list[tuple[str, str, list[tuple]]] = [
    ("expert", "/", [("text", "待审核题目"), ("text", "制度传达进度"), ("min", "nav", 1), ("min", "kpi", 4)]),
    ("expert", "/documents", [("text", "上传制度"), ("min", "rows", 1)]),
    ("expert", "/review", [("text", "题目审核")]),
    ("expert", "/questions", [("text", "题库管理")]),
    ("expert", "/statistics/question-quality", [("text", "题目质量分析"), ("text", "错误率"), ("min", "kpi", 4)]),
    ("hr_admin", "/", [("text", "进行中考试"), ("text", "考试完成进度"), ("min", "kpi", 4)]),
    ("hr_admin", "/exams", [("text", "考试管理"), ("min", "rows", 1)]),
    ("hr_admin", "/exams/create", [("text", "智能组卷")]),
    (
        "hr_admin",
        "/statistics",
        [("text", "章节正确率排行"), ("text", "部门平均分"), ("text", "导出合规报告 PDF"), ("min", "kpi", 6)],
    ),
    ("dept_manager", "/", [("text", "本部门"), ("min", "kpi", 4)]),
    ("dept_manager", "/statistics/dept", [("text", "部门统计"), ("text", "薄弱章节"), ("min", "kpi", 5)]),
    ("compliance", "/statistics/compliance", [("text", "合规全景"), ("min", "kpi", 4)]),
    # 合规角色现在可进入统计看板（后端 overview/dashboard 本就开放），但只应看到题目维度
    ("compliance", "/statistics", [("text", "章节正确率排行"), ("min", "kpi", 6)]),
    ("it_admin", "/system", [("text", "系统管理"), ("text", "审计日志")]),
    ("employee", "/my-exams", [("text", "我的考试"), ("min", "kpi", 4)]),
    ("employee", "/profile", [("text", "个人中心"), ("text", "基本信息"), ("min", "kpi", 4)]),
]

SNAPSHOT_JS = """(() => {
  const t = (document.body.innerText || '');
  return JSON.stringify({
    text: t,
    height: document.body.scrollHeight,
    nav: document.querySelectorAll('.app-side .app-nav').length,
    activeNav: document.querySelectorAll('.app-side .app-nav.active').length,
    kpi: document.querySelectorAll('.kpi').length,
    panel: document.querySelectorAll('.panel').length,
    rows: document.querySelectorAll('.ant-table-tbody tr').length,
    errAlert: document.querySelectorAll('.ant-alert-error').length,
    spinning: document.querySelectorAll('.ant-spin-spinning').length,
    skeletons: document.querySelectorAll('.ant-skeleton').length,
    crash: t.includes('Internal Server Error') || t.includes('Failed to resolve import')
  });
})()"""


# ── 极简 WebSocket（只用标准库，避免引入 websocket-client 依赖）──────────


class WS:
    def __init__(self, url: str):
        _, rest = url.split("://", 1)
        host_port, path = rest.split("/", 1)
        host, port = host_port.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败")
            resp += chunk
        if b"101" not in resp.split(b"\r\n")[0]:
            raise RuntimeError(f"WebSocket 被拒绝：{resp[:160]!r}")
        self.buf = resp.split(b"\r\n\r\n", 1)[1]
        self.msg_id = 0

    def _recv(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("WebSocket 已关闭")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, method: str, params: dict | None = None):
        self.msg_id += 1
        data = json.dumps({"id": self.msg_id, "method": method, "params": params or {}}).encode()
        header = bytearray([0x81])
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        return self.msg_id

    def wait(self, msg_id: int, timeout: float = 40):
        end = time.time() + timeout
        while time.time() < end:
            b1, b2 = self._recv(2)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv(8))[0]
            payload = self._recv(length)
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == msg_id:
                return msg
        raise TimeoutError(f"CDP 无响应：{msg_id}")


# ── 环境 ───────────────────────────────────────────────────────────────


def _http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ensure_services() -> bool:
    ok = True
    for label, url in (("后端", f"{BACKEND}/health"), ("Web 管理端", f"{WEB}/")):
        reachable = _http_ok(url)
        print(f"  {'[成功]' if reachable else '[失败]'} {label:10s} {url}")
        ok = ok and reachable
    return ok


def ensure_chrome() -> None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=3):
            return
    except Exception:  # noqa: BLE001
        pass
    if not Path(CHROME).exists():
        raise RuntimeError(f"未找到 Chrome：{CHROME}")
    subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={PROFILE}",
            f"--remote-debugging-port={CDP_PORT}",
            "--window-size=1600,1000",
            "--hide-scrollbars",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(60):
        time.sleep(1)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=3):
                return
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("Chrome 启动超时")


def login(role: str) -> str:
    """取开发登录 Token。

    后端开启了 --reload，改代码后会短暂重启（连接被重置）。
    这里做重试，避免把「后端正在重载」误判成 UI 缺陷。
    """
    last: Exception | None = None
    for _ in range(10):
        try:
            req = urllib.request.Request(
                f"{BACKEND}/api/v1/auth/dev-login?role={role}", method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())["access_token"]
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5)
    raise RuntimeError(f"开发登录失败（{role}）：{last}")


def new_target() -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# ── 主流程 ─────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", default="", help="截图输出目录（可留空只看断言）")
    parser.add_argument("--keep-chrome", action="store_true", help="结束后不关闭 Chrome")
    args = parser.parse_args()

    print("UI 渲染端到端校验（无头 Chrome + 真实后端数据）")
    print("─" * 66)
    if not ensure_services():
        print("  [失败] 服务未就绪：请先执行 python3 scripts/services.py start")
        return 1

    ensure_chrome()
    target = new_target()
    ws = WS(target["webSocketDebuggerUrl"])
    ws.wait(ws.send("Page.enable"))
    ws.wait(ws.send("Runtime.enable"))

    def evaluate(expr: str):
        res = ws.wait(ws.send("Runtime.evaluate", {"expression": expr, "returnByValue": True}))
        return res.get("result", {}).get("result", {}).get("value")

    if args.shots:
        Path(args.shots).mkdir(parents=True, exist_ok=True)

    tokens: dict[str, str] = {}
    current_role = ""
    failures: list[str] = []

    for role, path, asserts in CASES:
        if tokens.get(role) is None:
            tokens[role] = login(role)
            ws.wait(ws.send("Page.navigate", {"url": f"{WEB}/login"}))
            time.sleep(1.5)
            evaluate(
                "localStorage.setItem('token', %s);"
                "localStorage.setItem('user', JSON.stringify({id:'dev',name:'评审用户',role:%s}));'1'"
                % (json.dumps(tokens[role]), json.dumps(role))
            )
        current_role = role

        ws.wait(ws.send("Page.navigate", {"url": WEB + path}))
        # 轮询等待「首屏渲染完成 + 用户列表的数据请求落地」：
        # 只看 body 高度会误判（表格 loading 骨架也有高度），因此再等断言里的
        # 首个关键文案出现，避免把「数据还没回来」当成 UI 缺陷。
        want = next((a[1] for a in asserts if a[0] == "text"), "")
        # 用例声明了 KPI 下限时，必须等 KPI 渲染出来（否则只是「标题已出现」就截图，
        # 会把「请求还在飞」误报成缺陷 —— 这正是本脚本前两版误报的原因）
        need_kpi = max([a[2] for a in asserts if a[0] == "min" and a[1] == "kpi"] or [0])
        # 多数页面的首屏断言在标题（导航后立刻可见），标题出现 ≠ 数据已回来；
        # 因此额外要求「表格没有 loading 骨架」或「没有 loading 转圈」时才算稳定。
        snap: dict = {}
        stable = 0
        for _ in range(25):
            time.sleep(1.0)
            raw = evaluate(SNAPSHOT_JS)
            if not raw:
                continue
            snap = json.loads(raw)
            if snap.get("height", 0) > 200 and snap.get("nav", 0) > 0:
                if not want or want in snap.get("text", ""):
                    # 数据请求落地判据：页面无 .ant-spin-spinning 且 KPI/表格已出现
                    loaded = snap.get("spinning", 0) == 0 and snap.get("kpi", 0) >= need_kpi
                    if loaded and (snap.get("kpi", 0) > 0 or snap.get("rows", 0) > 0 or snap.get("panel", 0) > 1):
                        stable += 1
                        if stable >= 2:
                            break
                    else:
                        stable = 0

        text = snap.get("text", "")
        missing = [a[1] for a in asserts if a[0] == "text" and a[1] not in text]
        low: list[str] = []
        for a in asserts:
            if a[0] == "min":
                got = snap.get(a[1], 0)
                if got < a[2]:
                    low.append(f"{a[1]} 应≥{a[2]}，实际 {got}")
        crash = snap.get("crash") or snap.get("height", 0) < 200
        ok = not missing and not low and not crash

        flag = "PASS" if ok else "FAIL"
        print(
            f"  {flag}  [{role:12s}] {path:34s} kpi={snap.get('kpi')} panel={snap.get('panel')} "
            f"rows={snap.get('rows')} 页面错误提示={snap.get('errAlert')}"
            + (f"  ← 缺失 {missing} {low}" if not ok else "")
        )
        if not ok:
            failures.append(f"{role} {path}: 缺失={missing} {low} crash={crash}")
            print("        DOM 片段：" + text[:150].replace("\n", " | "))

        if args.shots and ok:
            res = ws.wait(
                ws.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            )
            name = f"{role}{path.replace('/', '-') or '-home'}.png"
            out = Path(args.shots) / name
            out.write_bytes(base64.b64decode(res["result"]["data"]))
            print(f"        截图：{out}（{out.stat().st_size // 1024} KB）")

    print("─" * 66)
    if failures:
        print(f"[失败] {len(failures)} / {len(CASES)} 个用例未通过")
        for f in failures:
            print("   " + f)
    else:
        print(f"[成功] 全部 {len(CASES)} 个用例通过（真实浏览器 + 真实接口数据）")

    if not args.keep_chrome:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/close/{target['id']}", timeout=5)
        except Exception:  # noqa: BLE001
            pass
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
