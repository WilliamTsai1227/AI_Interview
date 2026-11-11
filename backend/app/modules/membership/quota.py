"""會員方案與 token 額度。

兩層擋：
1. 軟上限（API 入口）：會開始新 AI 工作的 API（上傳履歷、新增職缺、出題、面試建議、職涯分析、開始面試）
   掛上 `Depends(require_tokens)`；本月用量 >= 上限就回 403 TOKEN_QUOTA_EXCEEDED，工作根本不會排進佇列。
2. 硬上限（LLMClient）：每次呼叫 OpenAI 前檢查用量 >= 上限 × hard_limit_ratio 就丟 QuotaExceeded。
   已經開始的面試（追問、轉錄、TTS、評分、報告）不在硬上限內，避免面試做到一半或報告出不來；
   軟上限與硬上限中間的空間就是給這些收尾工作用的。

用量在每次 LLM 呼叫成功後寫入 user_token_usage（與 llm_calls 同一個交易）。
"""
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends

from app.core import db
from app.core.errors import AppError
from app.core.security import CurrentUser, current_user

# 面試開始後的收尾工作：只受軟上限（入口）控制，不受硬上限切斷
FINISHING_PURPOSES = frozenset({"followup", "stt_quick", "stt", "tts", "evaluate", "report", "realtime"})

_STATUS_SQL = """
WITH u AS (
  SELECT id, plan AS raw_plan, plan_expires_at, token_limit_override,
         CASE WHEN plan_expires_at IS NOT NULL AND plan_expires_at <= now() THEN 'free' ELSE plan END AS plan
    FROM users WHERE id = $1
)
SELECT u.plan, u.raw_plan, u.plan_expires_at, p.name AS plan_name,
       COALESCE(u.token_limit_override, p.monthly_token_limit) AS token_limit,
       u.token_limit_override IS NOT NULL AS overridden,
       p.monthly_interview_limit AS interview_limit, p.hard_limit_ratio,
       COALESCE(t.input_tokens, 0) AS input_tokens, COALESCE(t.output_tokens, 0) AS output_tokens,
       COALESCE(t.total_tokens, 0) AS tokens_used,
       date_trunc('month', now()) AS period_start,
       date_trunc('month', now()) + interval '1 month' AS resets_at
  FROM u JOIN plans p ON p.code = u.plan
  LEFT JOIN user_token_usage t ON t.user_id = u.id AND t.period_start = date_trunc('month', now())::date
"""


@dataclass(frozen=True)
class Quota:
    plan: str                    # 實際生效的方案（過期 → free）
    plan_name: str
    raw_plan: str                # users.plan 上記的方案
    plan_expires_at: object
    token_limit: int | None      # None = 不限
    hard_limit: int | None
    tokens_used: int
    input_tokens: int
    output_tokens: int
    interview_limit: int | None
    overridden: bool
    period_start: object
    resets_at: object

    @property
    def tokens_remaining(self) -> int | None:
        return None if self.token_limit is None else max(0, self.token_limit - self.tokens_used)

    @property
    def soft_exceeded(self) -> bool:
        return self.token_limit is not None and self.tokens_used >= self.token_limit

    @property
    def hard_exceeded(self) -> bool:
        return self.hard_limit is not None and self.tokens_used >= self.hard_limit

    def out(self) -> dict:
        return {"plan": self.plan, "plan_name": self.plan_name, "plan_expires_at": self.plan_expires_at,
                "period_start": self.period_start, "resets_at": self.resets_at,
                "tokens_used": self.tokens_used, "tokens_limit": self.token_limit,
                "tokens_remaining": self.tokens_remaining, "interviews_limit": self.interview_limit}


class QuotaExceeded(Exception):
    """硬上限：LLMClient 丟出（app.ai.client 會包成 AIError 的子類別）。"""


async def status(conn: db.Connection, user_id: UUID) -> Quota | None:
    r = await conn.fetchrow(_STATUS_SQL, user_id)
    if not r:
        return None
    lim = r["token_limit"]
    return Quota(plan=r["plan"], plan_name=r["plan_name"], raw_plan=r["raw_plan"], plan_expires_at=r["plan_expires_at"],
                 token_limit=lim, hard_limit=None if lim is None else int(lim * float(r["hard_limit_ratio"])),
                 tokens_used=r["tokens_used"], input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
                 interview_limit=r["interview_limit"], overridden=r["overridden"],
                 period_start=r["period_start"], resets_at=r["resets_at"])


def exceeded_error(q: Quota) -> AppError:
    return AppError(403, "TOKEN_QUOTA_EXCEEDED", "這個月的 AI 額度已經用完了，下個月會自動重置",
                    {"plan": q.plan, "used": q.tokens_used, "limit": q.token_limit, "resets_at": q.resets_at.isoformat()})


async def ensure_tokens(conn: db.Connection, user_id: UUID) -> Quota | None:
    q = await status(conn, user_id)
    if q and q.soft_exceeded:
        raise exceeded_error(q)
    return q


async def require_tokens(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """FastAPI 依賴：放在會開始新 AI 工作的路由上（取代 current_user）。"""
    async with db.connection() as conn:
        await ensure_tokens(conn, user.id)
    return user


async def check_hard(user_id: UUID | None, purpose: str) -> None:
    """LLMClient 每次呼叫前檢查；背景工作（不是使用者直接觸發）也擋得住。"""
    if user_id is None or purpose in FINISHING_PURPOSES:
        return
    async with db.connection() as conn:
        q = await status(conn, user_id)
    if q and q.hard_exceeded:
        raise QuotaExceeded(f"user {user_id} over hard token limit ({q.tokens_used}/{q.hard_limit})")


async def record(conn: db.Connection, user_id: UUID, input_tokens: int | None, output_tokens: int | None,
                 cached_tokens: int | None = None) -> None:
    if not (input_tokens or output_tokens):
        return
    await conn.execute(
        """
        INSERT INTO user_token_usage (user_id, period_start, input_tokens, output_tokens, cached_tokens, call_count)
        VALUES ($1, date_trunc('month', now())::date, $2, $3, $4, 1)
        ON CONFLICT (user_id, period_start) DO UPDATE SET
          input_tokens  = user_token_usage.input_tokens  + EXCLUDED.input_tokens,
          output_tokens = user_token_usage.output_tokens + EXCLUDED.output_tokens,
          cached_tokens = user_token_usage.cached_tokens + EXCLUDED.cached_tokens,
          call_count    = user_token_usage.call_count + 1,
          updated_at    = now()
        """, user_id, input_tokens or 0, output_tokens or 0, cached_tokens or 0)
