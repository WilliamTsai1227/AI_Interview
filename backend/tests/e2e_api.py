"""端到端 API 測試：對執行中的 API＋worker 走完整流程（開發模式＝假 AI 也能跑）。

用法：python tests/e2e_api.py [base_url]
"""
import io
import sys
import time
import uuid

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api/v1"
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 2000   # 只需要通過 magic bytes 檢查


def docx_bytes() -> bytes:
    import docx
    d = docx.Document()
    for line in ["陳品妤", "前端工程師，3 年 React、TypeScript、Next.js 經驗。",
                 "Shoply Taiwan 前端工程師 2023-12 至今：結帳流程改版，轉換率提升 12%。",
                 "技能：React、TypeScript、Next.js、Jest、Figma"]:
        d.add_paragraph(line)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


class Api:
    def __init__(self):
        self.c = httpx.Client(base_url=BASE, timeout=30)
        self.token = None

    def req(self, method, path, expect=(200, 201, 202, 204), **kw):
        h = kw.pop("headers", {})
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        r = self.c.request(method, path, headers=h, **kw)
        assert r.status_code in expect, f"{method} {path} → {r.status_code} {r.text[:400]}"
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None

    def wait(self, fn, ok, what, timeout=40):
        t0 = time.time()
        while time.time() - t0 < timeout:
            v = fn()
            if ok(v):
                return v
            time.sleep(0.5)
        raise AssertionError(f"timeout waiting for {what}: {v}")

    def upload(self, purpose, data, ct, **extra):
        t = self.req("POST", "/uploads", json={"purpose": purpose, "content_type": ct, "size_bytes": len(data), **extra})
        r = httpx.put(t["url"], content=data, headers=t["headers"])
        assert r.status_code == 200, r.text
        return t["upload_id"]


def main():
    a = Api()
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    r = a.req("POST", "/auth/register", json={"email": email, "password": "pass12345", "display_name": "測試 小明"})
    a.token = r["access_token"]
    assert a.c.cookies.get("refresh_token"), "refresh cookie"
    r = a.req("POST", "/auth/refresh")
    a.token = r["access_token"]
    me = a.req("GET", "/me")
    assert me["onboarding"] == {"has_resume": False, "has_target": False, "has_completed_interview": False}
    print("✓ auth / me")

    up = a.upload("resume", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename="履歷.docx")
    res = a.req("POST", "/resumes", json={"upload_id": up})
    a.wait(lambda: a.req("GET", f"/resumes/{res['id']}"), lambda v: v["parse_status"] == "parsed", "resume parsed")
    a.req("POST", "/resumes", json={"upload_id": up}, expect=(409, 200))   # 單據已使用：同檔案回 200 或單據錯誤
    print("✓ resume upload + parse")

    a.req("POST", "/targets", json={"raw_text": "短"}, expect=(422,))
    jd = "Backend Engineer｜Acme Cloud\n工作內容：\n- 設計並維護 Python API\n- 優化 PostgreSQL 查詢\n條件要求：\n- 3 年以上 Python 經驗\n- 熟悉 Redis 與 Docker"
    t = a.req("POST", "/targets", json={"raw_text": jd})
    tid = t["id"]
    t = a.wait(lambda: a.req("GET", f"/targets/{tid}"), lambda v: v["question_set"]["status"] == "ready" and v["question_set"]["item_count"] >= 8, "questions ready")
    assert t["job"]["title"] == "Backend Engineer" and t["job"]["company_name"] == "Acme Cloud", t["job"]
    prep = a.wait(lambda: a.req("GET", f"/targets/{tid}/prep"), lambda v: v["status"] == "ready", "prep ready")
    assert prep["likely_questions"] and prep["basis"]["resume"], prep
    print(f"✓ target + {t['question_set']['item_count']} questions + prep (match {prep['match_score']})")

    qs = a.req("GET", f"/targets/{tid}/questions")["items"]
    # 追問規則：Q1 none、Q2 fixed 2、Q3 planned 2
    a.req("PUT", f"/targets/{tid}/questions/{qs[0]['id']}/followups", json={"mode": "none"})
    a.req("PUT", f"/targets/{tid}/questions/{qs[1]['id']}/followups", json={"mode": "fixed", "count": 2})
    r = a.req("PUT", f"/targets/{tid}/questions/{qs[2]['id']}/followups", json={"mode": "planned", "planned": ["為什麼選這個做法？", "怎麼衡量成果？"]})
    assert r["followup"]["count"] == 2 and len(r["followup"]["planned"]) == 2
    a.req("PUT", f"/targets/{tid}/questions/{qs[1]['id']}/followups", json={"mode": "fixed", "count": 9}, expect=(400,))
    ids = [q["id"] for q in qs]
    a.req("PUT", f"/targets/{tid}/questions/order", json={"item_ids": ids[1:] + ids[:1]})
    a.req("PUT", f"/targets/{tid}/questions/order", json={"item_ids": ids})
    added = a.req("POST", f"/targets/{tid}/questions", json={"text": "你怎麼帶新人？", "category": "團隊合作"})
    a.req("DELETE", f"/targets/{tid}/questions/{added['id']}")
    g = a.req("POST", f"/targets/{tid}/questions/generate", json={"note": "多問系統設計", "count": 3})
    a.wait(lambda: a.req("GET", f"/question-generations/{g['id']}"), lambda v: v["status"] == "succeeded", "generation")
    pg = a.req("POST", f"/preps/{prep['id']}/likely-questions/generate", json={"note": "帶人經驗", "count": 3})
    a.wait(lambda: a.req("GET", f"/prep-generations/{pg['generation_id']}"), lambda v: v["status"] == "succeeded", "prep more")
    a.req("POST", f"/preps/{prep['id']}/likely-questions/add-to-set", json={})
    print("✓ question bank: followup rules, reorder, add/delete, generate, prep more, add-to-set")

    r = a.req("POST", "/interviews", json={"target_id": tid, "length_mode": "quick", "persona": "warm", "language": "zh",
                                           "recording_consent": True})
    sid = r["session"]["id"]
    a.req("POST", "/interviews", json={"target_id": tid, "recording_consent": True}, expect=(409,))
    s = a.wait(lambda: a.req("GET", f"/interviews/{sid}"), lambda v: v["status"] == "ready", "session ready")
    r = a.req("POST", f"/interviews/{sid}/start", json={"expected_version": s["state_version"]})
    s = r["session"]
    assert r["intro"]["text"].startswith("你好"), r["intro"]

    log = []
    paused_once = False
    while s["status"] == "in_progress":
        q = s["current_question"]
        s = a.req("POST", f"/interviews/{sid}/questions/{q['id']}/playback-finished", json={"expected_version": s["state_version"]})["session"]
        if not paused_once:      # 暫停／繼續
            s = a.req("POST", f"/interviews/{sid}/pause", json={"expected_version": s["state_version"], "reason": "user"})["session"]
            assert s["status"] == "paused"
            s = a.req("POST", f"/interviews/{sid}/resume", json={"expected_version": s["state_version"]})["session"]
            assert s["phase"] == "awaiting_answer", s["phase"]
            paused_once = True
        r = a.req("POST", f"/interviews/{sid}/questions/{q['id']}/answers", json={"expected_version": s["state_version"]})
        att, s = r["attempt"], r["session"]
        if q["kind"] == "main" and q["order_no"] == 1 and att["attempt_no"] == 1:      # 重錄一次
            s = a.req("POST", f"/interviews/{sid}/attempts/{att['id']}/discard", json={"expected_version": s["state_version"]})["session"]
            r = a.req("POST", f"/interviews/{sid}/questions/{q['id']}/answers", json={"expected_version": s["state_version"]})
            att, s = r["attempt"], r["session"]
            assert att["attempt_no"] == 2
        upl = a.upload("answer_audio", WEBM, "audio/webm", attempt_id=att["id"])
        key = str(uuid.uuid4())
        body = {"upload_id": upl, "expected_version": s["state_version"], "client_duration_ms": 42000,
                "client_transcript": f"我在上一份工作負責 {q['category']} 相關的專案，嗯，然後把 API 回應時間從 800ms 降到 200ms，提升了 75%。"}
        r = a.req("POST", f"/interviews/{sid}/attempts/{att['id']}/complete", json=body, headers={"Idempotency-Key": key})
        r2 = a.req("POST", f"/interviews/{sid}/attempts/{att['id']}/complete", json=body, headers={"Idempotency-Key": key})
        assert r2["next"]["type"] == r["next"]["type"], "idempotent replay"
        nxt, s = r["next"], r["session"]
        label = f"Q{q['order_no']}" + (f"-F{q['followup_no']}" if q["kind"] == "followup" else "")
        log.append((label, q.get("followup_mode"), nxt["type"], (nxt.get("question") or {}).get("followup_source")))
    print("  sequence:", log)
    kinds = [x[0] for x in log]
    assert kinds[:1] == ["Q1"] and "Q2-F1" in kinds and "Q2-F2" in kinds and "Q3-F1" in kinds and "Q3-F2" in kinds, kinds
    assert "Q1-F1" not in kinds and "Q2-F3" not in kinds, kinds
    assert s["status"] == "completed", s["status"]
    print("✓ interview: none / fixed 2 / planned 2 follow-ups, pause/resume, re-record, idempotent complete")

    rep = a.wait(lambda: a.req("GET", f"/interviews/{sid}/report"), lambda v: v["status"] == "ready", "report ready", 60)
    q2 = rep["items"][1]
    assert len(q2["followups"]) == 2 and q2["group"]["score"] is not None, q2
    m, fa = q2["group"]["main_score"], q2["group"]["followup_avg"]
    assert q2["group"]["score"] == round(m * 0.6 + fa * 0.4), q2["group"]
    assert rep["overall_score"] == round(sum(i["group"]["score"] for i in rep["items"] if i["group"]) / len([i for i in rep["items"] if i["group"]]))
    lst = a.req("GET", "/interviews")
    assert lst["items"][0]["id"] == sid and lst["items"][0]["report_status"] == "ready"
    au = a.req("GET", f"/interviews/{sid}/answers/{q2['answer']['attempt_id']}/audio")
    assert au["url"]
    r = a.req("POST", f"/interviews/{sid}/report/add-to-set", json={})
    print(f"✓ report: overall {rep['overall_score']}, Q2 group {q2['group']}, add-to-set {r}")

    jobs = a.req("GET", "/jobs?filters=foreign")
    assert jobs["total"] >= 1 and all(j["source_url"] for j in jobs["items"])
    j = a.req("GET", f"/jobs/{jobs['items'][0]['id']}")
    t2 = a.req("POST", "/targets", json={"job_post_id": j["id"]})
    t3 = a.req("POST", "/targets", json={"job_post_id": j["id"]})
    assert t2["id"] == t3["id"]
    a.req("DELETE", f"/targets/{t2['id']}")
    print("✓ jobs search + add catalog job as target + archive")

    a.req("DELETE", f"/interviews/{sid}")
    a.req("GET", f"/interviews/{sid}/report", expect=(404,))
    a.req("POST", "/auth/logout")
    print("ALL OK")


if __name__ == "__main__":
    main()
