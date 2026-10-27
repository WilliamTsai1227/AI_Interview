"""選用：用 OpenAI 寫 2–3 句摘要與重點條列（自己的話，不轉貼原文）。沒有金鑰就略過。

只用標準庫呼叫 Responses API（Structured Outputs），方便打包成 Lambda。
"""
import json
import urllib.request

from .config import Config

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "duties", "requirements"],
    "properties": {
        "summary": {"type": "string", "description": "2–3 句、≤ 120 字，介紹公司與這個職位"},
        "duties": {"type": "array", "items": {"type": "string"}, "description": "3–5 點工作重點，每點 ≤ 30 字，用自己的話改寫"},
        "requirements": {"type": "array", "items": {"type": "string"}, "description": "3–5 點條件重點，每點 ≤ 30 字，用自己的話改寫"},
    },
}
SYSTEM = ("你是職缺整理助手。根據職缺內容，用繁體中文寫出簡短摘要與重點條列。"
          "一律用自己的話改寫，不要逐字複製原文，不要加入原文沒有的資訊。")


def summarize(cfg: Config, title: str, company: str, description: str) -> dict | None:
    if not cfg.summarize_enabled or not description:
        return None
    body = {
        "model": cfg.summary_model,
        "input": [{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": f"職稱：{title}\n公司：{company}\n職缺內容：\n{description[:6000]}"}],
        "text": {"format": {"type": "json_schema", "name": "job_summary", "schema": SCHEMA, "strict": True}},
    }
    if cfg.summary_model.startswith("gpt-5"):
        body["reasoning"] = {"effort": "minimal"}
    req = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {cfg.openai_api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        text = next(c["text"] for o in data.get("output", []) if o.get("type") == "message"
                    for c in o.get("content", []) if c.get("type") == "output_text")
        out = json.loads(text)
        return {"summary": out["summary"][:600], "duties": [d[:120] for d in out["duties"][:6]],
                "requirements": [d[:120] for d in out["requirements"][:6]]}
    except Exception:
        return None
