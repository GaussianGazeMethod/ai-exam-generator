#!/usr/bin/env python3
"""开发服务管理 — 启动/停止/状态检查（就绪轮询，不用固定 sleep）。

用法：
    python scripts/services.py start     启动后端容器 + 前端
    python scripts/services.py stop      停止两者
    python scripts/services.py restart   重启
    python scripts/services.py status    查看状态

设计要点（修正此前两个坏习惯）：
1. **不猜时间，轮询探测就绪**：实测 Vite 冷启动约 1s、后端约 3s，
   固定 sleep 8 是数倍浪费；这里以 0.2s 间隔探测，就绪即返回。
2. **按端口/精确 PID 停止**：不再用 `pkill -f vite` 这类宽泛匹配，
   避免误杀机器上其他 node 进程（如 DSH harness、MCP server）。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_PORT = 8000

# 两个前端：Web 管理端（5173，考试管理/审核/统计）与移动答题端（5174，考生扫码答题）。
# 此前只管理 5173，移动端要手工启动，容易漏启导致扫码后打不开。
FRONTEND_PORT = 5173  # 兼容既有引用
FRONTEND_DIR = PROJECT_ROOT / "web"
FRONTENDS: list[tuple[int, str, Path]] = [
    (5173, "Web 管理端", PROJECT_ROOT / "web"),
    (5174, "移动答题端", PROJECT_ROOT / "mobile"),
]

READY_TIMEOUT = 60.0  # 秒
POLL_INTERVAL = 0.2  # 秒


# ──────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────


def _pids_on_port(port: int) -> list[int]:
    """返回监听指定端口的进程 PID 列表（精确，不误伤）。"""
    try:
        out = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    return [int(x) for x in out.split() if x.strip().isdigit()]


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_ready(label: str, url: str, *, timeout: float = READY_TIMEOUT) -> tuple[bool, float]:
    """轮询直到 URL 可达，返回 (是否就绪, 耗时秒)。"""
    started = time.time()
    while time.time() - started < timeout:
        if _http_ok(url):
            return True, time.time() - started
        time.sleep(POLL_INTERVAL)
    return False, time.time() - started


def _compose(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


# ──────────────────────────────────────────────────────────
# 启停
# ──────────────────────────────────────────────────────────


def start() -> int:
    print("启动服务（就绪轮询模式）")
    print("─" * 58)

    # 1) 后端容器
    t0 = time.time()
    result = _compose(["up", "-d"])
    if result.returncode != 0:
        print(f"  [失败] docker compose up 失败：{result.stderr.strip()[:200]}")
        return 1

    ok, elapsed = wait_ready("backend", f"http://localhost:{BACKEND_PORT}/health")
    mark = "[成功]" if ok else "[失败]"
    print(f"  {mark} 后端容器  就绪 {elapsed:5.2f}s  http://localhost:{BACKEND_PORT}")
    if not ok:
        print(f"      容器日志：{_compose(['logs','--tail=15','backend']).stdout[-500:]}")

    # 2) 前端（Web 管理端 + 移动答题端）
    for port, label, directory in FRONTENDS:
        if _pids_on_port(port):
            print(f"  [成功] {label}  已在运行      http://localhost:{port}")
            continue
        if not (directory / "node_modules").exists():
            print(f"  [注意]  {label}  未安装依赖（{directory}/node_modules 缺失），跳过启动")
            continue
        env = {**os.environ}
        log_path = Path(f"/tmp/ai_exam_vite_{port}.log")
        with open(log_path, "w") as log:
            subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(directory),
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        ok, elapsed = wait_ready(label, f"http://localhost:{port}/")
        mark = "[成功]" if ok else "[失败]"
        print(f"  {mark} {label}  就绪 {elapsed:5.2f}s  http://localhost:{port}")
        if not ok:
            print(f"      启动日志：{log_path}")

    print("─" * 58)
    print(f"  总计 {time.time() - t0:.2f}s")
    return 0


def stop() -> int:
    print("停止服务（按端口精确定位，不使用宽泛 pkill）")
    print("─" * 58)

    for port, label, _dir in FRONTENDS:
        pids = _pids_on_port(port)
        if not pids:
            print(f"  · {label}      未在运行")
            continue
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"  [成功] {label}      已停止 (PID {pid})")
            except ProcessLookupError:
                print(f"  · {label}      PID {pid} 已退出")
            except PermissionError:
                print(f"  [失败] {label}      PID {pid} 无权限停止")

    result = _compose(["down"])
    if result.returncode == 0:
        print("  [成功] 后端容器  已停止")
    else:
        print(f"  [失败] 后端容器  停止失败：{result.stderr.strip()[:160]}")

    print("─" * 58)
    return 0


def status() -> int:
    print("服务状态")
    print("─" * 58)

    result = _compose(["ps", "--format", "json"])
    backend_state = "未运行"
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue
            if info.get("Service") == "backend":
                backend_state = f"{info.get('State')} ({info.get('Status')})"

    backend_http = _http_ok(f"http://localhost:{BACKEND_PORT}/health")

    print(f"  后端容器  {backend_state}")
    print(f"  后端 HTTP http://localhost:{BACKEND_PORT}  {'[成功] 可达' if backend_http else '[失败] 不可达'}")
    for port, label, _dir in FRONTENDS:
        pids = _pids_on_port(port)
        http_ok = _http_ok(f"http://localhost:{port}/")
        suffix = f" (PID {','.join(map(str, pids))})" if http_ok and pids else ""
        print(
            f"  {label} http://localhost:{port}  "
            f"{'[成功] 可达' + suffix if http_ok else '[失败] 不可达'}"
        )

    if backend_http:
        try:
            req = urllib.request.Request(
                f"http://localhost:{BACKEND_PORT}/api/v1/auth/dev-login", method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                token = json.loads(resp.read())["access_token"]
            req2 = urllib.request.Request(
                f"http://localhost:{BACKEND_PORT}/api/v1/generate/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req2, timeout=10) as resp:
                st = json.loads(resp.read())
            print(
                f"  AI 能力   LLM={st['llm_available']} "
                f"嵌入={st['embedding_available']} "
                f"向量库={st['vector_store'].get('available')}"
                f"({st['vector_store'].get('count')} 条)"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  AI 能力   查询失败：{type(exc).__name__}")

    print("─" * 58)
    return 0


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else "status"
    if action == "start":
        return start()
    if action == "stop":
        return stop()
    if action == "restart":
        stop()
        return start()
    if action == "status":
        return status()
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))