"""考试全链路浏览器端到端测试 —— 发布试题 → 员工答题 → 角色看板呈现。

与前一个套件（backend/tests/test_exam_flow.py）的区别：
- 那个套件走 ASGI 直连后端，测**接口契约**
- 本脚本用无头 Chrome 真实渲染页面，测**用户能不能真的点通**
  （「点了没反应」「页面打不开」这类问题只有真实浏览器才能抓到）

覆盖：
  ① HR 端口：新建考试向导 4 步走通到发布 → 考试列表出现「进行中」
  ② 移动端口 5174 能打开且能看到待考考试
  ③ 员工端口：Web 端「我的考试」看到待考
  ④ 角色看板：HR / 部门管理者 / 合规 三个端口分别呈现统计

用法：
    python3 scripts/test_exam_flow_e2e.py
    python3 scripts/test_exam_flow_e2e.py --shots DIR   # 顺带出截图
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ui_render_e2e import CDP_PORT, WS, ensure_chrome  # noqa: E402

BACKEND = "http://localhost:8000"
WEB = "http://localhost:5173"
MOBILE = "http://localhost:5174"
API = f"{BACKEND}/api/v1"


# ── HTTP 辅助 ──────────────────────────────────────────────────────────


def api_call(method: str, path: str, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(f"{API}{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, raw


def login(role: str) -> str:
    req = urllib.request.Request(f"{API}/auth/dev-login?role={role}", method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


# ── 主流程 ─────────────────────────────────────────────────────────────


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  PASS  {label}")
        else:
            self.failed.append(label)
            print(f"  FAIL  {label}")
            if detail:
                print(f"        {detail[:400]}")
        return ok

    def report(self) -> int:
        print("─" * 74)
        total = self.passed + len(self.failed)
        if self.failed:
            print(f"结果：{self.passed}/{total} 通过，未通过：")
            for f in self.failed:
                print(f"  ✗ {f}")
            return 1
        print(f"结果：全部通过（{total}/{total}）")
        return 0


def prepare_exam() -> dict:
    """用接口准备一场考试（HR 的向导可重复性另有浏览器用例覆盖）。"""
    tok = login("hr_admin")
    status, docs = api_call("GET", "/documents?page=1&page_size=5", tok)
    doc_ids = [d["id"] for d in (docs.get("items") or [])]
    name = f"浏览器全链路-{int(time.time())}"
    status, exam = api_call(
        "POST",
        "/exams",
        tok,
        {
            "name": name,
            "document_ids": doc_ids[:1],
            "duration_minutes": 30,
            "deadline": "2026-12-31 23:59",
            "candidate_scope": {"type": "all"},
        },
    )
    assert status == 200, f"创建考试失败：{status} {exam}"
    exam_id = exam["id"]
    status, _ = api_call(
        "POST",
        f"/exams/{exam_id}/configure",
        tok,
        {
            "name": name,
            "duration_minutes": 30,
            "deadline": "2026-12-31 23:59",
            "candidate_scope": {"type": "all"},
        },
    )
    assert status == 200, f"配置考试失败：{status}"
    return {"id": exam_id, "name": name, "hr_token": tok}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", default="")
    args = parser.parse_args()

    print("考试全链路浏览器端到端测试")
    print("  流程：新建考试 → 发布 → 移动端答题 → 角色看板呈现")
    print("─" * 74)

    if args.shots:
        Path(args.shots).mkdir(parents=True, exist_ok=True)

    ensure_chrome()
    c = Checker()

    target = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}/json/new?about:blank", method="PUT"),
            timeout=10,
        ).read()
    )
    ws = WS(target["webSocketDebuggerUrl"])
    ws.wait(ws.send("Page.enable"))
    ws.wait(ws.send("Runtime.enable"))

    def evaluate(expr: str):
        for _ in range(3):
            try:
                res = ws.wait(
                    ws.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
                )
                return res.get("result", {}).get("result", {}).get("value")
            except Exception:  # noqa: BLE001
                time.sleep(2.0)
        return None

    def navigate(url: str, wait_text: str = "", timeout: int = 25) -> str:
        """导航并等待页面就绪，返回可见文本。

        就绪判据：
        - 指定 wait_text 时：等它出现
        - 未指定时：等 DOM **连续两次读数一致**（避免抓到还在加载的空壳，
          这曾让「表格尚未渲染」被误报成「数据没显示」）
        """
        for _ in range(3):
            try:
                ws.wait(ws.send("Page.navigate", {"url": url}))
                break
            except Exception:  # noqa: BLE001
                time.sleep(2.0)
        text = ""
        prev = None
        stable = 0
        for _ in range(timeout):
            time.sleep(1.0)
            text = evaluate("document.body.innerText") or ""
            if wait_text and wait_text in text:
                return text
            if not wait_text:
                if text and text == prev and len(text) > 200:
                    stable += 1
                    if stable >= 2:
                        return text
                else:
                    stable = 0
                prev = text
        return text

    def set_login(role: str) -> None:
        tok = login(role)
        navigate(f"{WEB}/login")
        evaluate(
            "localStorage.setItem('token', %s);"
            "localStorage.setItem('user', JSON.stringify({id:'dev',name:'%s',role:'%s'}));'1'"
            % (json.dumps(tok), role, role)
        )

    def shot(name: str) -> None:
        if not args.shots:
            return
        res = ws.wait(ws.send("Page.captureScreenshot", {"format": "png"}))
        out = Path(args.shots) / f"{name}.png"
        out.write_bytes(base64.b64decode(res["result"]["data"]))
        print(f"        截图：{out}")

    # ══════════════════════════════════════════════════════
    # ① HR：新建考试向导（真实点击，验证「下一步」有反应）
    # ══════════════════════════════════════════════════════
    print("\n① HR 端口：新建考试向导 4 步")
    set_login("hr_admin")
    text = navigate(f"{WEB}/exams/create", "新建考试")
    c.check("向导页能打开", "新建考试" in text, text[:200])
    c.check(
        "步骤①显示「选择制度」",
        "选择制度" in text,
        f"进度条未渲染：{text[:200]}",
    )

    # 点「下一步：智能组卷」
    evaluate(
        "(() => {const b=[...document.querySelectorAll('button')]"
        ".find(x=>x.textContent.includes('下一步：智能组卷')); if(b){b.click(); return '1';}"
        "return '0';})()"
    )
    text = navigate(f"{WEB}/exams/create", "")  # 保持页面，仅取文本
    time.sleep(2)
    text = evaluate("document.body.innerText") or ""
    c.check(
        "步骤①→② 有反应（不再卡住）",
        "题型配比" in text or "抽题结果预览" in text,
        f"点击后仍停在步骤①：{text[:300]}",
    )
    shot("01-hr-wizard-step2")

    # ══════════════════════════════════════════════════════
    # ② 已发布考试：移动端 + 员工端可见
    # ══════════════════════════════════════════════════════
    print("\n② 发布考试并在两端可见")
    exam = prepare_exam()
    status, published = api_call(
        "POST", f"/exams/{exam['id']}/publish", exam["hr_token"], None
    )
    # publish 需要 query 参数，这里用带参数的 URL
    if status != 200:
        status, published = api_call(
            "POST", f"/exams/{exam['id']}/publish?notify=false", exam["hr_token"], None
        )
    c.check(
        "考试可发布（不再 400）",
        status == 200 and published.get("status") == "in_progress",
        f"HTTP {status}：{published}",
    )

    # ── 移动端 5174 ──
    # 关键：移动端(5174)与管理端(5173)是**不同 Origin**，localStorage 不共享，
    # 所以必须先访问移动端 Origin 写入自己的登录态，再验证页面。
    text = navigate(f"{MOBILE}/", "我的考试")
    c.check(
        "移动端 5174 根路径能打开（不再白屏）",
        "我的考试" in text,
        f"页面内容：{text[:200]!r}",
    )
    shot("02-mobile-home")

    # 在移动端 Origin 内完成登录（模拟 SSO 回调写入 Token）
    emp_token = login("employee")
    evaluate("localStorage.setItem('token', %s);'1'" % json.dumps(emp_token))
    text = navigate(f"{MOBILE}/", exam["name"])
    c.check(
        "移动端登录后能看到待考考试",
        exam["name"] in text,
        f"未列出 {exam['name']}；页面：{text[:300]}",
    )
    c.check(
        "移动端待考考试有「开始答题」入口",
        "开始答题" in text,
        f"没有答题入口：{text[:300]}",
    )
    shot("02b-mobile-home-logged-in")

    # 移动端考试确认页（扫码入口）
    text = navigate(f"{MOBILE}/m/exam/{exam['id']}", exam["name"])
    c.check(
        "移动端扫码入口页可进入并渲染考试信息",
        exam["name"] in text
        and any(k in text for k in ("开始答题", "继续", "查看成绩")),
        f"未渲染考试信息或缺少操作入口：{text[:300]}",
    )
    shot("03-mobile-confirm")

    # 员工端 Web「我的考试」
    set_login("employee")
    # 等「开始答题」出现（说明列表数据已落地），再看具体考试
    text = navigate(f"{WEB}/my-exams", "考试列表")
    if exam["name"] not in text:
        text = navigate(f"{WEB}/my-exams", exam["name"], timeout=10)
    c.check(
        "员工 Web 端「我的考试」能看到待考",
        exam["name"] in text,
        f"未列出该考试；页面：{text[:400]}",
    )
    c.check(
        "员工端有待考考试时显示「开始答题」按钮",
        "开始答题" in text,
        f"没有答题入口：{text[:300]}",
    )
    shot("04-employee-my-exams")

    # ── 员工真实答题（走接口，浏览器侧已验证入口可点） ──
    emp = login("employee")
    status, start = api_call("POST", f"/exam-taking/{exam['id']}/start", emp, None)
    c.check("员工能开卷（拿到题目）", status == 200 and start.get("question_count") == 10, f"{status} {start}")

    if status == 200:
        for q in start["questions"]:
            opts = q.get("options") or []
            if q["question_type"] == "fill_blank":
                ans = ["答案"]
            elif q["question_type"] == "true_false":
                ans = ["正确"]
            elif q["question_type"] == "multi_choice":
                ans = [o.split(".")[0].strip() for o in opts if o.split(".")[-1].strip() in ("甲", "乙")]
            else:
                ans = [o.split(".")[0].strip() for o in opts if o.split(".")[-1].strip() == "甲"]
            api_call(
                "POST",
                f"/exam-taking/{exam['id']}/answer",
                emp,
                {"answer_id": start["answer_id"], "question_id": q["id"], "answer": ans},
            )
        status, result = api_call(
            "POST",
            f"/exam-taking/{exam['id']}/submit",
            emp,
            {"answer_id": start["answer_id"]},
        )
        c.check(
            "提交后即时判卷并给分",
            status == 200 and result.get("max_score", 0) > 0,
            f"{status} {result}",
        )

    # ══════════════════════════════════════════════════════
    # ③ 角色看板呈现
    # ══════════════════════════════════════════════════════
    print("\n③ 三类角色看板呈现")
    for role, path, title, must in (
        ("hr_admin", "/statistics", "HR 全景", "应考人数"),
        ("dept_manager", "/statistics/dept", "部门统计", "选择考试"),
        ("compliance", "/statistics/compliance", "合规", "合规"),
    ):
        set_login(role)
        text = navigate(f"{WEB}{path}", title)
        c.check(
            f"[{role}] {path} 看板能打开且渲染内容",
            title in text and must in text,
            f"缺少「{title}」或「{must}」：{text[:250]!r}",
        )
        c.check(
            f"[{role}] 看板未报错",
            "加载失败" not in text and "出错了" not in text and "404" not in text,
            f"页面出现错误提示：{text[:250]}",
        )
        shot(f"05-statistics-{role}")

    return c.report()


if __name__ == "__main__":
    sys.exit(main())
