"""临时校验：审核工作台是否正确显示 AI 质量自查（严格口径）提示。

只读地验证 UI 渲染，测试用题目在结束时删除。

运行：
    python3 scripts/_check_review_alert.py
"""

from __future__ import annotations

import base64
import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BACKEND = "http://localhost:8000"
WEB = "http://localhost:5173"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT = 9334
PROFILE = "/tmp/dsh-ui-check-profile"
DB = Path(__file__).resolve().parent.parent / "backend" / "app.db"

# 与生产口径一致的「分项与总评自相矛盾」提示文字
NOTE = (
    "[注意] AI 分项结论与总评自相矛盾（已按严格口径判为不通过，请重点复核） "
    "整体尚可 未通过项：选项不重复 问题：B 与 C 语义等价"
)


sys.path.insert(0, str(Path(__file__).resolve().parent))
# 复用 UI 渲染测试的裸 WebSocket 客户端：
# 走 websocket-client 会带上 Origin 头，Chrome 的 CDP 会以 403 拒绝握手。
from test_ui_render_e2e import WS  # noqa: E402


def login(role: str) -> str:
    req = urllib.request.Request(f"{BACKEND}/api/v1/auth/dev-login?role={role}", method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


def ensure_chrome() -> None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2):
            return
    except Exception:  # noqa: BLE001
        pass
    subprocess.Popen(
        [
            CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", f"--user-data-dir={PROFILE}",
            f"--remote-debugging-port={CDP_PORT}", "--window-size=1600,1000",
            "--hide-scrollbars", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    for _ in range(60):
        time.sleep(1)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=3):
                return
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("Chrome 启动超时")


def seed_question(kind: str) -> str:
    """在真实 app.db 中插入一道待审核题目，返回题目 id。

    kind = "contradiction"：分项有否 + 总评说通过（严格口径判为不通过）
    kind = "ok"          ：五项全通过

    注意：SQLAlchemy 的 Enum 默认按**枚举名**落库（`SINGLE_CHOICE`），不是值
    （`single_choice`）。用错会导致 ORM 反序列化失败、接口表现为「题目不存在」。
    """
    import uuid

    qid = f"uicheck-{kind}-{uuid.uuid4().hex[:8]}"
    con = sqlite3.connect(DB)
    con.execute(
        """
        INSERT INTO questions
          (id, question_type, status, content, options, answer, score,
           chapter_no, section_no, clause_no, ai_quality_pass, ai_quality_note,
           usage_count, is_active, created_at, updated_at)
        VALUES (?, 'SINGLE_CHOICE', 'PENDING', ?, ?, ?, 1.0,
                '第一章', NULL, '第一条', ?, ?, 0, 1, datetime('now'), datetime('now'))
        """,
        (
            qid,
            f"【UI 校验-{kind}】数据安全管理工作实行的是什么原则？",
            json.dumps(
                ["A. 分级管理、统一负责", "B. 统一管理、分级负责",
                 "C. 统一管理、分工负责", "D. 分级管理、分工负责"],
                ensure_ascii=False,
            ),
            json.dumps(["B"], ensure_ascii=False),
            0 if kind == "contradiction" else 1,
            NOTE if kind == "contradiction" else "题目与条款原文一致，五个校验项均通过。",
        ),
    )
    con.commit()
    con.close()
    return qid


def cleanup(ids: list[str] | None = None) -> int:
    """删除临时题目；同时清掉上次异常退出遗留的行（本脚本的题目一律以 uicheck- 前缀）。"""
    con = sqlite3.connect(DB)
    if ids:
        for qid in ids:
            con.execute("DELETE FROM questions WHERE id = ?", (qid,))
    removed = con.execute("DELETE FROM questions WHERE id LIKE 'uicheck-%'").rowcount
    con.commit()
    con.close()
    return removed


def main() -> int:
    ensure_chrome()
    leftover = cleanup()  # 先清掉上次异常退出的残留
    if leftover:
        print(f"（已清理上次残留的临时题目 {leftover} 条）")
    token = login("expert")
    ids = [seed_question("contradiction"), seed_question("ok")]

    target = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{CDP_PORT}/json/new?about:blank", method="PUT"
            ),
            timeout=10,
        ).read()
    )
    ws = WS(target["webSocketDebuggerUrl"])
    ws.wait(ws.send("Page.enable"))
    ws.wait(ws.send("Runtime.enable"))

    def evaluate(expr: str):
        res = ws.wait(ws.send("Runtime.evaluate", {"expression": expr, "returnByValue": True}))
        return res.get("result", {}).get("result", {}).get("value")

    ws.wait(ws.send("Page.navigate", {"url": f"{WEB}/login"}))
    time.sleep(1.5)
    evaluate(
        "localStorage.setItem('token', %s);"
        "localStorage.setItem('user', JSON.stringify({id:'dev',name:'评审用户',role:'expert'}));'1'"
        % json.dumps(token)
    )

    results: list[tuple[str, bool, str]] = []
    try:
        for qid, label, must_have, must_not_have in (
            (ids[0], "分项与总评矛盾 → 应显示警告", "AI 自查未通过", None),
            (ids[1], "五项全通过 → 应显示通过", "AI 自查通过", "AI 自查未通过"),
        ):
            for attempt in range(3):
                try:
                    ws.wait(ws.send("Page.navigate", {"url": f"{WEB}/review/{qid}"}))
                    break
                except Exception:  # noqa: BLE001 - 页面导航期间 CDP 可能短暂无响应
                    time.sleep(2.0)
            text = ""
            for _ in range(25):
                time.sleep(1.0)
                try:
                    text = evaluate("document.body.innerText") or ""
                except Exception:  # noqa: BLE001
                    continue
                if must_have in text:
                    break
            ok = must_have in text and (must_not_have is None or must_not_have not in text)
            # 顺带确认矛盾提示文字本身渲染出来了
            detail_ok = not (must_not_have is None and "自相矛盾" not in text)
            results.append((label, ok and detail_ok, text[:200].replace("\n", " | ")))
    finally:
        cleanup(ids)

    print("审核工作台 AI 质量自查提示渲染校验")
    print("─" * 66)
    failed = 0
    for label, ok, snippet in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failed += 1
            print(f"        DOM 片段：{snippet}")
    print("─" * 66)
    print(f"结果：{'全部通过' if not failed else f'{failed} 项未通过'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
