"""Live 即時對談：面試官簡報與指示、逐字稿切題、完整流程（建立 → 簡報 → 對談存檔 → 結束 → 切題 → 評分 → 報告）。

開發模式（假 AI）執行，不會呼叫 OpenAI；背景工作由測試直接執行，不需要 worker。
需要 docker compose 的 PostgreSQL 與 Redis。執行：.venv/bin/pytest tests/test_live_interview.py -q
"""
import datetime as dt
import uuid

import pytest

from app.ai.agents import interviewer as iva
from app.core import db
from app.core.config import settings
from app.workers import main as worker
from tests.conftest import API, register

JD = ("資深後端工程師\n公司：Acme Cloud\n工作內容：\n- 設計並維護 Python API\n- 優化 PostgreSQL 查詢\n"
      "條件要求：\n- 3 年以上 FastAPI 經驗\n- 熟悉 Redis 與 Docker")
RESUME = {"summary": "後端工程師 4 年", "skills": ["Python", "FastAPI", "PostgreSQL"],
          "projects": [{"name": "訂單系統重構", "description": "API 延遲從 800ms 降到 200ms"}], "achievements": [], "experiences": []}
TOPICS = [{"id": "t1", "category": "自我介紹", "title": "自我介紹"}, {"id": "t2", "category": "技術深度", "title": "訂單系統"}]


# ---------- 純函式 ----------
def test_instructions_include_brief_resume_and_job():
    brief = iva.fallback_brief({"title": "後端工程師", "requirements": ["FastAPI"]}, RESUME,
                               [{"category": "技術深度", "text": "請分享你最有成就感的專案", "expected_points": ["結果"]}], "standard")
    brief["concerns"] = [{"item": "2022 年空窗半年", "why": "確認原因"}]
    text = iva.instructions(brief=brief, job={"company_name": "Acme", "title": "後端工程師", "requirements": ["FastAPI"]},
                            resume=RESUME, persona="tough", language="zh", length_mode="standard", candidate="小明")
    assert "Acme" in text and "小明" in text
    assert "訂單系統重構" in text                  # 履歷亮點與履歷內容都在指示裡
    assert "2022 年空窗半年" in text
    assert "請分享你最有成就感的專案" in text       # 題庫變成議程的開場問法
    assert "15 分鐘" in text
    assert "不評分" in text


def test_brief_topics_keep_seed_rubric():
    rubric = {"version": "r1", "criteria": [], "must_mention": ["延遲"]}
    seeds = [{"source_item_id": str(uuid.uuid4()), "category": "技術深度", "difficulty": "advanced", "text": "談談效能優化",
              "competency": "效能", "expected_points": ["量測方式"], "rubric": rubric}]
    b = iva.fallback_brief({}, None, seeds, "quick")
    t = b["topics"][0]
    assert t["id"] == "t1" and t["rubric"] == rubric and t["source_item_id"] == seeds[0]["source_item_id"]
    assert t["difficulty"] == "advanced"


def test_clean_rejects_bad_seqs_and_promotes_followup():
    turns = [{"seq": 0, "role": "interviewer", "text": "請自我介紹"}, {"seq": 1, "role": "candidate", "text": "我是小明", "started_ms": 1000, "ended_ms": 4000},
             {"seq": 2, "role": "interviewer", "text": "訂單系統怎麼優化？"}, {"seq": 3, "role": "candidate", "text": "加了快取"},
             {"seq": 4, "role": "interviewer", "text": "怎麼量的？"}, {"seq": 5, "role": "candidate", "text": "看 p95"}]
    out = iva.SegmentOut(groups=[
        iva.Group(topic_id="t1", category="自我介紹", main=iva.Exchange(question="請自我介紹", answer_seqs=[1, 0, 99]), followups=[]),
        # 主題目沒有回答（seq 1 已用過）→ 追問頂上；topic_id 不存在 → 空字串；類型不合法 → 情境題
        iva.Group(topic_id="t9", category="亂寫", main=iva.Exchange(question="重複", answer_seqs=[1]),
                  followups=[iva.Exchange(question="訂單系統怎麼優化？", answer_seqs=[3]), iva.Exchange(question="怎麼量的？", answer_seqs=[5])]),
        iva.Group(topic_id="t2", category="技術深度", main=iva.Exchange(question="沒人回答", answer_seqs=[]), followups=[]),
    ])
    groups = iva._clean(out, turns, TOPICS)
    assert len(groups) == 2
    assert groups[0]["main"] == {"question": "請自我介紹", "answer": "我是小明", "duration_ms": 3000}
    assert groups[1]["topic_id"] == "" and groups[1]["category"] == "情境題"
    assert groups[1]["main"]["answer"] == "加了快取" and [f["answer"] for f in groups[1]["followups"]] == ["看 p95"]


def test_fallback_segment_pairs_turns():
    turns = [{"seq": 0, "role": "interviewer", "text": "你好，請自我介紹"}, {"seq": 1, "role": "candidate", "text": "我是小明"},
             {"seq": 2, "role": "candidate", "text": "做後端四年"}, {"seq": 3, "role": "interviewer", "text": "談談 Redis"},
             {"seq": 4, "role": "candidate", "text": "用來做快取"}]
    groups = iva.fallback_segment(turns, TOPICS)
    assert [g["main"]["answer"] for g in groups] == ["我是小明 做後端四年", "用來做快取"]
    assert [g["topic_id"] for g in groups] == ["t1", "t2"]


def test_exchanges_need_next_interviewer_turn():
    turns = [{"seq": 0, "role": "interviewer", "text": "你好，聽得到嗎？"}, {"seq": 1, "role": "candidate", "text": "聽得到"},
             {"seq": 2, "role": "interviewer", "text": "好的。"}, {"seq": 3, "role": "interviewer", "text": "請介紹你最有成就感的專案"},
             {"seq": 4, "role": "candidate", "text": "我重構了訂單系統，"}, {"seq": 5, "role": "candidate", "text": "延遲從 800ms 降到 200ms"},
             {"seq": 6, "role": "interviewer", "text": "怎麼做到的？"}, {"seq": 7, "role": "candidate", "text": "加了 Redis 快取，還調整了索引"}]
    ex = iva.exchanges(turns)
    assert [e["seq"] for e in ex] == [4]            # 「聽得到」太短；最後一段 Ava 還沒接話
    assert ex[0]["answer_seqs"] == [4, 5] and ex[0]["question"].endswith("請介紹你最有成就感的專案")


# ---------- 完整流程 ----------
@pytest.fixture()
def dev_ai(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")      # 假 AI


def _drain(client, since: dt.datetime, rounds: int = 10) -> None:
    """執行這個測試建立的背景工作（直到沒有新的）。"""
    async def run_once() -> int:
        async with db.connection() as conn:
            jobs = await conn.fetch(
                "SELECT id, kind, payload FROM background_jobs WHERE status IN ('queued','retrying') AND created_at >= $1 ORDER BY id", since)
        for j in jobs:
            await worker.HANDLERS[j["kind"]](j["payload"])
            async with db.connection() as conn:
                await conn.execute("UPDATE background_jobs SET status = 'succeeded', finished_at = now() WHERE id = $1", j["id"])
        return len(jobs)
    for _ in range(rounds):
        if not client.portal.call(run_once):
            return


def test_live_flow_end_to_end(client, dev_ai):
    since = dt.datetime.now(dt.timezone.utc)
    user, _ = register(client, f"live-{uuid.uuid4().hex[:8]}@example.com")
    tid = client.post(f"{API}/targets", headers=user, json={"raw_text": JD}).json()["id"]
    _drain(client, since)
    assert client.get(f"{API}/targets/{tid}", headers=user).json()["question_set"]["status"] == "ready"

    r = client.post(f"{API}/interviews", headers=user, json={"target_id": tid, "length_mode": "quick", "persona": "real",
                                                              "voice_mode": "live", "recording_consent": True})
    assert r.status_code == 201, r.text
    s = r.json()["session"]
    sid = s["id"]
    assert s["voice_mode"] == "live" and s["status"] == "preparing" and s["live"]["brief_ready"] is False
    assert r.json()["questions"] == []          # live 不預先建立題目
    _drain(client, since)
    s = client.get(f"{API}/interviews/{sid}", headers=user).json()
    assert s["status"] == "ready" and s["live"]["brief_ready"] and s["live"]["topics"]
    assert s["live"]["time_limit_sec"] == 8 * 60

    r = client.post(f"{API}/interviews/{sid}/start", headers=user, json={"expected_version": s["state_version"]})
    assert r.status_code == 200 and r.json()["intro"] is None
    # 開發模式沒有金鑰：連不上 GPT-Live，回 503 讓前端提示
    r = client.post(f"{API}/interviews/{sid}/live/connect", headers=user, json={"sdp": "v=0\r\no=- fake"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "LIVE_UNAVAILABLE"

    turns = [("interviewer", "你好，我是 Ava，請先自我介紹。"), ("candidate", "我是小明，做後端四年，主要用 FastAPI。"),
             ("interviewer", "你提到訂單系統重構，延遲怎麼降下來的？"), ("candidate", "我加了 Redis 快取，p95 從 800 毫秒降到 200 毫秒。")]
    body = {"turns": [{"seq": i, "role": role, "text": t, "started_ms": i * 5000, "ended_ms": i * 5000 + 4000}
                      for i, (role, t) in enumerate(turns)]}
    assert client.post(f"{API}/interviews/{sid}/live/turns", headers=user, json=body).status_code == 204
    assert client.post(f"{API}/interviews/{sid}/live/turns", headers=user, json=body).status_code == 204   # 重送不重複
    s = client.get(f"{API}/interviews/{sid}", headers=user).json()
    assert s["live"]["turn_count"] == 4 and s["live"]["next_seq"] == 4

    # 面試中的即時評分：Ava 又開口才算答完，所以只有第一段；第二段還在答
    _drain(client, since)
    fb = client.get(f"{API}/interviews/{sid}/feedback", headers=user).json()
    assert fb["provisional"] is True and len(fb["items"]) == 1
    assert fb["items"][0]["status"] == "done" and fb["items"][0]["score"] is not None

    # 暫停 → 繼續：live 沒有逐題 phase
    s = client.post(f"{API}/interviews/{sid}/pause", headers=user, json={"expected_version": s["state_version"]}).json()["session"]
    assert s["status"] == "paused"
    s = client.post(f"{API}/interviews/{sid}/resume", headers=user, json={"expected_version": s["state_version"]}).json()["session"]
    assert s["status"] == "in_progress" and s["phase"] == "idle"

    r = client.post(f"{API}/interviews/{sid}/end", headers=user, json={"reason": "completed"}).json()
    assert r["session"]["status"] == "completed" and r["report"]["status"] == "processing"
    assert client.post(f"{API}/interviews/{sid}/live/turns", headers=user, json=body).status_code == 409

    _drain(client, since)
    rep = client.get(f"{API}/interviews/{sid}/report", headers=user).json()
    assert rep["status"] == "ready", rep
    assert rep["overall_score"] is not None
    s = client.get(f"{API}/interviews/{sid}", headers=user).json()
    assert s["progress"]["answered"] == 2 and s["progress"]["total_main"] == 2
    # 職缺頁的「報告」分頁：依職缺篩選
    mine = client.get(f"{API}/interviews", headers=user, params={"target_id": tid}).json()
    assert [i["id"] for i in mine["items"]] == [sid] and mine["summary"]["total"] == 1
    other = client.get(f"{API}/interviews", headers=user, params={"target_id": str(uuid.uuid4())}).json()
    assert other["items"] == [] and other["summary"]["total"] == 0
    fb = client.get(f"{API}/interviews/{sid}/feedback", headers=user).json()     # 切完題後改讀正式評分
    assert fb["provisional"] is False and len(fb["items"]) == 2 and all(i["status"] == "done" for i in fb["items"])


def test_live_without_candidate_speech_is_aborted(client, dev_ai):
    since = dt.datetime.now(dt.timezone.utc)
    user, _ = register(client, f"live-{uuid.uuid4().hex[:8]}@example.com")
    tid = client.post(f"{API}/targets", headers=user, json={"raw_text": JD}).json()["id"]
    _drain(client, since)
    sid = client.post(f"{API}/interviews", headers=user, json={"target_id": tid, "voice_mode": "live",
                                                                "recording_consent": True}).json()["session"]["id"]
    _drain(client, since)
    s = client.get(f"{API}/interviews/{sid}", headers=user).json()
    client.post(f"{API}/interviews/{sid}/start", headers=user, json={"expected_version": s["state_version"]})
    client.post(f"{API}/interviews/{sid}/live/turns", headers=user,
                json={"turns": [{"seq": 0, "role": "interviewer", "text": "你好，請自我介紹"}]})
    r = client.post(f"{API}/interviews/{sid}/end", headers=user, json={"reason": "user_ended"}).json()
    assert r["session"]["status"] == "aborted" and r["report"] is None


def test_avatar_connect_gives_token_only_when_configured(client, dev_ai, monkeypatch):
    """擬真人像：沒設定 Simli 回 503（前端用插畫人像）；有設定時換 token，次數有上限。"""
    from app.ai import client as ai
    from app.modules.interviews import avatar

    monkeypatch.setattr(settings, "simli_api_key", "")       # 不受本機 .env 影響
    since = dt.datetime.now(dt.timezone.utc)
    user, _ = register(client, f"avatar-{uuid.uuid4().hex[:8]}@example.com")
    tid = client.post(f"{API}/targets", headers=user, json={"raw_text": JD}).json()["id"]
    _drain(client, since)
    sid = client.post(f"{API}/interviews", headers=user, json={"target_id": tid, "length_mode": "quick", "persona": "real",
                                                                "voice_mode": "live", "recording_consent": True}).json()["session"]["id"]
    _drain(client, since)
    s = client.get(f"{API}/interviews/{sid}", headers=user).json()
    assert s["avatar"] is None
    r = client.post(f"{API}/interviews/{sid}/avatar/connect", headers=user, json={})
    assert r.status_code == 503 and r.json()["error"]["code"] == "AVATAR_UNAVAILABLE"

    calls = []

    async def fake_session(*, max_session_sec, user_id=None, session_id=None):
        calls.append(max_session_sec)
        return {"session_token": "tok", "ice_servers": [{"urls": ["stun:example.org"]}]}

    monkeypatch.setattr(settings, "simli_api_key", "test-key")
    monkeypatch.setattr(ai, "simli_session", fake_session)
    monkeypatch.setattr(avatar, "MAX_CONNECTS", 2)
    assert client.get(f"{API}/interviews/{sid}", headers=user).json()["avatar"] == "simli"
    r = client.post(f"{API}/interviews/{sid}/avatar/connect", headers=user, json={})
    assert r.status_code == 200 and r.json() == {"provider": "simli", "session_token": "tok",
                                                 "ice_servers": [{"urls": ["stun:example.org"]}]}
    assert calls == [8 * 60 + avatar.LIVE_EXTRA_SEC]      # 只開到 Live 時間上限＋5 分鐘
    assert client.post(f"{API}/interviews/{sid}/avatar/connect", headers=user, json={}).status_code == 200
    r = client.post(f"{API}/interviews/{sid}/avatar/connect", headers=user, json={})
    assert r.status_code == 429
