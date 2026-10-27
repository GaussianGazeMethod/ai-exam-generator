"""端到端 API 测试 — 验证「上传制度 → 解析 → AI 出题 → 待审核」完整链路。

在容器内运行：
    docker run --rm -v "<项目路径>/backend":/app/backend -w /app/backend \
        ai-exam-backend:1 python scripts/test_api_e2e.py

覆盖 spec 链路一（制度上传 → AI 出题 → 专家审核）的全部后端接口：
    POST   /api/v1/auth/dev-login           开发登录
    POST   /api/v1/documents                上传制度（真实解析 + 向量索引）
    GET    /api/v1/documents/{id}           制度详情（含章节树）
    GET    /api/v1/documents/{id}/questions 该制度关联题目
    POST   /api/v1/generate/questions       触发出题（异步任务）
    GET    /api/v1/generate/tasks/{task_id} 轮询任务进度
    GET    /api/v1/questions/pending        待审核题目列表
    POST   /api/v1/questions/{id}/approve   审核通过
    POST   /api/v1/questions/{id}/reject    弃用
    DELETE /api/v1/documents/{id}           删除制度（含清理向量）
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  {'[成功]' if ok else '[失败]'} {name}" + (f" — {detail}" if detail else ""))


def make_docx(path: str) -> None:
    """生成测试用制度文档（含章/节/条层级）。"""
    import docx

    d = docx.Document()
    d.add_heading("数据安全管理规范", 0)
    d.add_paragraph("为规范公司数据安全管理，保护客户信息安全，特制定本规范。")

    d.add_heading("第一章 总则", level=1)
    d.add_paragraph("第一条 本规范适用于公司全体员工及外包人员。")
    d.add_paragraph("第二条 数据安全管理工作实行统一管理、分级负责的原则。")

    d.add_heading("第二章 数据分类标准", level=1)
    d.add_paragraph("第三条 公司数据分为公开数据、内部数据、敏感数据三级。")
    d.add_paragraph("第四条 客户身份证号、银行账号、联系方式属于敏感数据，须加密存储。")

    d.add_heading("第三章 数据加密要求", level=1)
    d.add_heading("第一节 存储加密", level=2)
    d.add_paragraph("第五条 敏感数据必须采用 AES-256 算法加密后存储。")
    d.add_paragraph(
        "第六条 禁止将客户数据存储在个人设备上，包括个人电脑、手机、U盘等便携存储介质。"
    )
    d.add_paragraph("第七条 加密密钥须每 90 天轮换一次。")

    d.add_heading("第四章 违规处理", level=1)
    d.add_paragraph("第八条 违反本规范的，视情节轻重给予警告、记过直至解除劳动合同。")

    d.save(path)


async def main() -> int:
    print("=" * 74)
    print("端到端 API 测试：制度上传 → 解析 → AI 出题 → 待审核")
    print("=" * 74)

    # 使用独立的数据库与向量目录，避免污染开发数据
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./e2e_test.db")
    for stale in ("./e2e_test.db",):
        if os.path.exists(stale):
            os.remove(stale)

    import app.config as config_module

    config_module.get_settings.cache_clear()

    from app.database import init_db
    from app.main import app

    await init_db()

    # ── 启动真实 uvicorn 服务器 ──
    # 不用 httpx.ASGITransport：它会在同一次 ASGI 调用内等 BackgroundTasks 跑完才返回，
    # 导致「触发接口立即返回」与「进度可轮询」这两个关键行为无法被真实观测。
    # 真实服务器才是生产路径，这里按生产路径验证。
    import uvicorn

    port = 8123
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    server_task = asyncio.create_task(server.serve())

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if getattr(server, "started", False):
            break
        await asyncio.sleep(0.1)
    else:
        print("[失败] uvicorn 启动超时")
        return 1
    print(f"     已启动测试服务器 {base_url}")

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=180.0) as client:
            return await run_checks(client)
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=10)
        except asyncio.TimeoutError:
            server_task.cancel()


async def run_checks(client: httpx.AsyncClient) -> int:
    """在真实 HTTP 连接上执行全部接口检查。"""
    # ── 1. 开发登录 ──
    print("\n【1】认证")
    resp = await client.post("/api/v1/auth/dev-login")
    check("开发登录成功", resp.status_code == 200, f"HTTP {resp.status_code}")
    if resp.status_code != 200:
        return 1
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = await client.get("/api/v1/auth/me", headers=headers)
    role = me.json().get("role") if me.status_code == 200 else "?"
    check("当前用户信息可读", me.status_code == 200, f"role={role}")

    # 专家角色才能上传与审核
    user_id = me.json().get("id")
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.models.user import User, UserRole

    async with async_session_factory() as db:
        u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if u is not None:
            u.role = UserRole.EXPERT
            await db.commit()
    resp = await client.post("/api/v1/auth/dev-login")
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("     （已将会话用户切换为 expert 角色）")

    # ── 2. 上传制度（真实解析）──
    print("\n【2】上传制度文档（真实解析 + 向量索引）")
    docx_path = "/tmp/e2e_policy.docx"
    make_docx(docx_path)

    with open(docx_path, "rb") as fh:
        files = {
            "file": (
                "data_policy.docx",
                fh,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        }
        resp = await client.post(
            "/api/v1/documents",
            params={"name": "数据安全管理规范", "version": "3.0"},
            files=files,
            headers=headers,
        )
    check("上传接口返回成功", resp.status_code == 200,
          f"HTTP {resp.status_code} {resp.text[:120]}")
    if resp.status_code != 200:
        return 1
    doc = resp.json()
    doc_id = doc["id"]
    print(f"     制度ID={doc_id}  状态={doc['status']}  页数={doc['page_count']}")

    # ── 3. 校验解析结果 ──
    print("\n【3】校验解析结果（章节树）")
    import json as _json

    structured = _json.loads(doc.get("structured_content") or "{}")
    stats = structured.get("stats", {})
    print(f"     解析统计：{stats}")
    print(f"     解析方式：{structured.get('parse_method')}")
    check("状态为已解析", doc["status"] == "parsed", doc["status"])
    check("识别到章节结构", stats.get("chapter_count", 0) >= 4,
          f"章数={stats.get('chapter_count')}")
    check("识别到条款", stats.get("clause_count", 0) >= 8,
          f"条数={stats.get('clause_count')}")
    check("结构化内容非空", bool(structured.get("chapters")))

    # ── 4. 触发 AI 出题（关键：验证立即返回）──
    print("\n【4】触发 AI 出题（异步任务，应立即返回）")
    import time as _time

    t0 = _time.time()
    resp = await client.post(
        "/api/v1/generate/questions",
        params={"document_id": doc_id, "max_points": 8},
        headers=headers,
    )
    trigger_secs = _time.time() - t0
    check("触发接口返回成功", resp.status_code == 200,
          f"HTTP {resp.status_code} {resp.text[:160]}")
    if resp.status_code != 200:
        return 1
    task_id = resp.json()["task_id"]
    print(f"     任务ID={task_id}")
    print(f"     触发耗时 {trigger_secs:.2f}s（应远小于出题总耗时）")
    check("触发接口立即返回（<3s，未被出题阻塞）", trigger_secs < 3.0,
          f"{trigger_secs:.2f}s")
    check("返回状态为未完成", resp.json()["status"] in ("pending", "running"),
          resp.json()["status"])

    # ── 5. 轮询任务进度 ──
    print("\n【5】轮询任务进度")
    final = None
    seen_progress: list[int] = []
    for _ in range(900):  # 最多等 9 分钟（0.6s 一次采样）
        r = await client.get(f"/api/v1/generate/tasks/{task_id}", headers=headers)
        if r.status_code != 200:
            break
        data = r.json()
        if data["progress"] not in seen_progress:
            seen_progress.append(data["progress"])
            print(f"     [{data['progress']:3d}%] {data['status']:9s} {data['message']}")
        if data["status"] in ("completed", "failed"):
            final = data
            break
        await asyncio.sleep(0.6)

    check("任务达到终态", final is not None, "超时未结束" if final is None else "")
    if final is None:
        return 1
    check("任务执行成功", final["status"] == "completed",
          f"{final['status']}: {final['message']}")
    print(f"     生成题目数={final['generated_count']}  考点数={final['key_point_count']}")
    print(f"     题型分布={final['type_stats']}")
    if final.get("errors"):
        print(f"     告警（前3条）：{final['errors'][:3]}")
    check("产出题目", final["generated_count"] > 0, f"{final['generated_count']} 题")
    check("进度被多次推进（轮询可观测）", len(seen_progress) >= 2,
          f"采样到 {len(seen_progress)} 个进度点：{seen_progress}")

    # ── 6. 待审核列表 ──
    print("\n【6】待审核题目列表")
    resp = await client.get("/api/v1/questions/pending", headers=headers)
    check("待审核列表可读", resp.status_code == 200, f"HTTP {resp.status_code}")
    pending = resp.json()
    print(f"     待审核题目：{pending['total']} 道")
    check("待审核列表有数据", pending["total"] > 0, f"{pending['total']} 道")

    first_q = pending["items"][0] if pending["items"] else None
    if first_q:
        print(f"     首题：[{first_q['question_type']}] {first_q['content'][:44]}")
        print(f"     来源：{first_q.get('chapter_no')} {first_q.get('clause_no')}")
        check("题目带来源标注",
              bool(first_q.get("chapter_no") or first_q.get("clause_no")))
        check("题目状态为待审核", first_q["status"] == "pending")

        # ── 7. 审核流转 ──
        print("\n【7】审核流转（通过 / 弃用）")
        r_ok = await client.post(
            f"/api/v1/questions/{first_q['id']}/approve", headers=headers
        )
        check("审核通过成功", r_ok.status_code == 200,
              f"HTTP {r_ok.status_code} {r_ok.text[:120]}")
        if r_ok.status_code == 200:
            check("状态变为已通过", r_ok.json()["status"] == "approved")

        if len(pending["items"]) > 1:
            second = pending["items"][1]
            r_no = await client.post(
                f"/api/v1/questions/{second['id']}/reject",
                params={"reason": "考点太偏（自动化测试）"},
                headers=headers,
            )
            check("弃用成功", r_no.status_code == 200,
                  f"HTTP {r_no.status_code}")
            if r_no.status_code == 200:
                check("状态变为已弃用", r_no.json()["status"] == "rejected")
                check("记录了弃用原因", bool(r_no.json().get("reject_reason")))

    # ── 8. 制度关联题目 ──
    print("\n【8】制度关联题目接口")
    resp = await client.get(f"/api/v1/documents/{doc_id}/questions", headers=headers)
    check("关联题目可读", resp.status_code == 200, f"HTTP {resp.status_code}")
    if resp.status_code == 200:
        check("关联题目数量匹配", resp.json()["total"] == final["generated_count"],
              f"{resp.json()['total']} vs {final['generated_count']}")

    # ── 9. AI 状态 ──
    print("\n【9】AI 能力状态")
    resp = await client.get("/api/v1/generate/status", headers=headers)
    check("AI 状态可读", resp.status_code == 200)
    if resp.status_code == 200:
        st = resp.json()
        print(f"     LLM={st['llm_available']} 嵌入={st['embedding_available']} "
              f"向量数={st['vector_store'].get('count')}")
        check("LLM 可用", st["llm_available"])
        check("知识库可用", st["vector_store"].get("available") is True)
        check("向量库已索引条款", st["vector_store"].get("count", 0) > 0,
              f"{st['vector_store'].get('count')} 条")

    # ── 10. 删除制度（含清理）──
    print("\n【10】删除制度（级联清理）")
    resp = await client.delete(f"/api/v1/documents/{doc_id}", headers=headers)
    check("删除成功", resp.status_code == 200, f"HTTP {resp.status_code}")
    resp = await client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    check("删除后不可读", resp.status_code == 404, f"HTTP {resp.status_code}")

    total = len(PASSED) + len(FAILED)
    print("\n" + "=" * 74)
    print(f"结果：通过 {len(PASSED)}/{total}")
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print(f"  · {f}")
    print("=" * 74)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))