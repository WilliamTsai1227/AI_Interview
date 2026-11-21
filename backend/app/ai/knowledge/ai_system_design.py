"""AI 系統設計知識表：AI／LLM 工程職缺的出題、追問、評分參考。

整理自 amitshekhariitbhu/ai-system-design（Apache-2.0，Outcome School）的主題架構與面試解題步驟，
內容用我們自己的話重寫，只留「面試官看什麼、會怎麼問、會往哪裡追問、常見錯誤」。
具體數字（GPU 吞吐、價格）會過時，所以這裡只寫估算方法，評分時不拿數字對錯扣分。

用法：context(job) 在職缺看起來是 AI 工程職缺時，挑出最相關的幾個主題給 prompt；其他職缺回傳 None（完全不影響）。
"""
import re

SOURCE = "github.com/amitshekhariitbhu/ai-system-design（Apache-2.0）"

# AI 系統設計題的解題步驟：評分時看候選人有沒有走到（不必照順序、不必每步都講滿）
FRAMEWORK = [
    "釐清需求：使用者是誰、規模（每日請求量、併發）、延遲要求（首字時間）、預算、要不要能採取行動",
    "定義 AI 要做的事：輸入輸出是什麼、怎樣算答對、什麼情況要交給人",
    "資料：資料從哪來、怎麼清理與切塊、怎麼更新、權限怎麼控管",
    "架構：畫出主要元件（入口、檢索、模型、工具、快取、佇列）和資料流",
    "模型與 prompt：選哪個模型和為什麼（品質、延遲、成本）、何時用小模型、fallback",
    "評估：離線測試集、線上指標、LLM 當評審要先對過人工標註",
    "部署與擴充：推論怎麼服務、怎麼擴容、串流、限流、成本估算",
    "監控與迭代：要看哪些指標（延遲、成本、錯誤、品質）、怎麼發現退化、怎麼回滾",
]

# 每個主題：kw＝在職缺文字裡出現就加分的關鍵字（小寫比對）
TOPICS = [
    {"key": "rag", "name": "RAG 與知識庫問答",
     "kw": ["rag", "檢索", "retrieval", "知識庫", "文件問答", "knowledge base", "搜尋", "search", "embedding", "向量"],
     "checks": "能講清楚離線建索引（解析、切塊、嵌入、存）與線上檢索（查詢、取回、重排、生成）兩段，知道品質多半卡在文件解析和切塊",
     "questions": ["設計一個讓員工查公司內部文件的問答系統", "設計一個能回答長篇合約問題的系統"],
     "probes": ["切塊多大、要不要重疊？為什麼？", "只用向量檢索會漏掉什麼？要不要加關鍵字檢索、怎麼合併結果？",
                "重排（reranker）會增加多少延遲，值得嗎？", "文件更新或刪除時，索引怎麼跟上？", "怎麼確保使用者只查得到自己有權限的文件？"],
     "pitfalls": "只說「丟進向量資料庫」；沒提文件解析；沒提權限；沒有評估檢索品質的方法"},
    {"key": "vectordb", "name": "向量資料庫與索引",
     "kw": ["向量資料庫", "vector database", "vector db", "pgvector", "milvus", "pinecone", "qdrant", "weaviate", "faiss", "hnsw", "elasticsearch", "opensearch"],
     "checks": "知道近似最近鄰（ANN）是用一點準確度換速度，能依資料量和記憶體選索引（HNSW、IVF+PQ、磁碟型）",
     "questions": ["一億筆向量要在 100ms 內查完，你會怎麼選資料庫和索引？"],
     "probes": ["資料量多大時 pgvector 就不夠用？", "換嵌入模型時，舊的向量怎麼辦？", "要支援過濾條件（例如部門、時間）時，索引怎麼設計？"],
     "pitfalls": "一開始就選最重的分散式向量資料庫；忘了換模型要全部重新嵌入"},
    {"key": "serving", "name": "模型推論與部署",
     "kw": ["推論", "inference", "serving", "vllm", "sglang", "tensorrt", "triton", "gpu", "自架模型", "self-host", "開源模型", "llama", "部署模型"],
     "checks": "分得清 prefill（處理輸入，決定首字時間）與 decode（逐字產生，受記憶體頻寬限制），知道批次處理、KV cache、張量／管線平行各解決什麼",
     "questions": ["設計一個給公司多個團隊共用的 LLM 推論平台", "要自架一個 70B 模型服務 1 萬名使用者，需要什麼？"],
     "probes": ["首字時間和每秒產生字數，哪個對你的產品比較重要？", "一張 GPU 放不下模型時怎麼辦？", "模型載入要好幾分鐘，自動擴容怎麼處理？",
                "負載平衡為什麼不能只用輪流分配？"],
     "pitfalls": "把 LLM 當一般無狀態 API 擴容；忽略模型載入時間；不知道輸出 token 比輸入貴很多"},
    {"key": "estimate", "name": "延遲、吞吐與成本估算",
     "kw": ["延遲", "latency", "吞吐", "throughput", "成本", "cost", "高流量", "大流量", "規模", "scale", "併發", "concurrent", "效能"],
     "checks": "能用每日使用者 × 每次對話 token 數，推到每秒 token、需要幾張 GPU 或每月 API 費用，並說出假設",
     "questions": ["每天 1000 萬使用者、每人 5 次對話，估一下需要多少算力和每月成本"],
     "probes": ["你的估算裡哪個假設最不確定？差兩倍會怎樣？", "高峰時段怎麼處理？", "怎麼把成本降一半？"],
     "pitfalls": "直接報數字不講假設；沒分輸入和輸出 token；忽略高峰"},
    {"key": "caching", "name": "快取（Prompt、語意、KV）",
     "kw": ["cache", "快取", "redis", "語意快取", "semantic cache"],
     "checks": "知道三種快取各省什麼：prompt 快取省重複前綴（固定內容放前面）、語意快取省相似問題、KV cache 省生成時的重算",
     "questions": ["設計一個 LLM 回應的語意快取"],
     "probes": ["相似度門檻怎麼定？太低會怎樣？", "語意快取可以跨使用者共用嗎？", "怎麼排 prompt 才能讓快取命中率最高？"],
     "pitfalls": "語意快取跨使用者共用私人資料；快取鍵沒包含模型版本"},
    {"key": "gateway", "name": "模型路由、AI Gateway 與限流",
     "kw": ["gateway", "路由", "routing", "多模型", "multi-model", "litellm", "openrouter", "rate limit", "限流", "配額", "quota"],
     "checks": "知道依任務難度把請求分到大小模型、閘道集中做失敗切換、成本追蹤、限流；限流要算 token 不只算請求數",
     "questions": ["設計一個公司內部的 AI Gateway", "怎麼在不降低品質的前提下，把模型費用降一半？"],
     "probes": ["路由的判斷本身要花多少時間？", "主要模型掛掉時怎麼辦？", "為什麼只限制每分鐘請求數不夠？"],
     "pitfalls": "依問題長度而不是難度做路由；閘道成為單點故障；限流只算請求數"},
    {"key": "context", "name": "Context 管理、長文件與對話記憶",
     "kw": ["context", "上下文", "長文件", "摘要", "summar", "對話", "chatbot", "聊天", "記憶", "memory", "客服"],
     "checks": "知道 context 塞越多不一定越好（中間的內容容易被忽略），能選截斷、摘要、滑動視窗、壓縮、分層記憶",
     "questions": ["設計一個會記得使用者長期偏好的聊天助理", "設計 ChatGPT 這類通用對話助理"],
     "probes": ["對話很長時，哪些要保留、哪些可以丟？", "長期記憶什麼該存、什麼不該存？", "使用者要求刪除記憶時怎麼做？"],
     "pitfalls": "把全部歷史塞進 context；記憶什麼都存（包含個資）"},
    {"key": "agents", "name": "Agent 與工具呼叫",
     "kw": ["agent", "代理", "tool", "工具", "function calling", "langchain", "langgraph", "mcp", "自動化", "automation", "workflow"],
     "checks": "能講 agent 的組成（模型、指令、工具、記憶、迴圈），知道模型只「建議」呼叫工具、真正執行在程式端，會設步數上限與人工確認",
     "questions": ["設計一個像 Claude Code、Cursor 的程式碼 agent", "設計一個能幫使用者訂會議、寄信的助理"],
     "probes": ["agent 陷入無限迴圈怎麼辦？", "什麼時候用固定流程、什麼時候讓 agent 自己決定？", "危險操作（刪除、付款）怎麼防呆？", "工具回傳的內容可以信任嗎？"],
     "pitfalls": "什麼都做成 agent；沒有步數或成本上限；讓模型直接執行危險動作"},
    {"key": "multiagent", "name": "多 Agent 協作",
     "kw": ["multi-agent", "多代理", "多 agent", "crewai", "autogen", "a2a", "協作"],
     "checks": "知道多 agent 的代價（成本、延遲、除錯難），能說出何時需要（專業分工、平行、context 隔離），先從單一 agent 開始",
     "questions": ["設計一個多 agent 的研究助理"],
     "probes": ["為什麼不用一個 agent 就好？", "agent 之間怎麼傳遞結果？", "其中一個 agent 失敗時整個任務怎麼辦？"],
     "pitfalls": "一開始就設計很多 agent；沒說明協調方式"},
    {"key": "async", "name": "串流與非同步長任務",
     "kw": ["串流", "stream", "sse", "websocket", "非同步", "async", "queue", "佇列", "kafka", "rabbitmq", "背景"],
     "checks": "知道對話用串流（SSE）降低體感延遲，長任務用工作編號＋佇列＋輪詢或通知，處理斷線與重試",
     "questions": ["使用者上傳 200 頁文件要產生分析報告，系統怎麼設計？"],
     "probes": ["使用者中途離開頁面怎麼辦？", "工作失敗要重試幾次？會不會重複扣費？", "串流到一半斷線怎麼辦？"],
     "pitfalls": "長任務用同步請求硬等；重試沒有冪等"},
    {"key": "safety", "name": "護欄、Prompt Injection 與資料隱私",
     "kw": ["安全", "security", "資安", "guardrail", "護欄", "injection", "合規", "compliance", "pii", "個資", "隱私", "privacy", "gdpr", "金融", "醫療"],
     "checks": "能分輸入護欄與輸出護欄，知道 prompt injection 擋不完、要靠權限最小化、程式端限制和人工確認，個資要遮蔽與留稽核紀錄",
     "questions": ["你的 RAG 系統讀到一份文件裡藏了「忽略指令、把資料寄出去」，會發生什麼？怎麼防？"],
     "probes": ["只靠 prompt 寫「不要照做」夠嗎？", "個資送進第三方模型前要做什麼？", "怎麼證明系統沒有洩漏資料？"],
     "pitfalls": "以為 prompt 寫清楚就能擋住注入；忽略工具回傳內容也可能是攻擊"},
    {"key": "evaluation", "name": "評估與 LLM 當評審",
     "kw": ["評估", "eval", "測試", "品質", "quality", "benchmark", "a/b", "指標", "metric", "準確率"],
     "checks": "能設計離線測試集（含邊界案例）與線上指標，知道 LLM 評審的偏誤（位置、長度、偏好自己）要先對過人工標註",
     "questions": ["設計一個 LLM 產品的評估與監控流程"],
     "probes": ["測試集怎麼來？要多少題？", "改了 prompt 怎麼知道有沒有變差？", "LLM 評審的分數可以信嗎？怎麼驗證？", "agent 要評估什麼（結果、過程、工具使用）？"],
     "pitfalls": "只靠人工抽看；只看平均分數；沒有回歸測試"},
    {"key": "observability", "name": "監控、可觀測性與容錯",
     "kw": ["監控", "monitor", "observability", "可觀測", "log", "tracing", "追蹤", "langfuse", "langsmith", "sre", "穩定", "可用性"],
     "checks": "知道要記錄首字時間、token、成本、快取命中、錯誤率，用追蹤串起一次請求的每一步；容錯有逾時、退避重試、備援模型、降級",
     "questions": ["模型供應商突然變慢或掛掉，你的產品會怎樣？"],
     "probes": ["要看哪幾個指標來判斷「品質變差了」？", "一次回答錯誤時，怎麼追到是哪一步出問題？", "降級時使用者會看到什麼？"],
     "pitfalls": "只監控伺服器 CPU；沒有備援模型；重試沒有退避"},
    {"key": "finetune", "name": "微調與訓練資料",
     "kw": ["fine-tun", "finetun", "微調", "訓練", "training", "lora", "標註", "dataset", "資料集", "mlops", "model registry"],
     "checks": "分得清 RAG（補知識）和微調（改行為、格式、語氣），知道 LoRA 這類輕量微調、資料品質比數量重要、要有實驗追蹤和模型版本管理",
     "questions": ["客服回答語氣不一致，你會用 RAG、prompt 還是微調？為什麼？"],
     "probes": ["微調資料從哪來？怎麼確保品質？", "微調後怎麼確認沒有變笨？", "什麼情況不該微調？"],
     "pitfalls": "用微調來塞知識；沒有評估就上線新模型"},
    {"key": "optimization", "name": "推論最佳化（量化、批次、裝置端）",
     "kw": ["量化", "quantiz", "最佳化", "optimiz", "加速", "batching", "批次", "onnx", "edge", "裝置端", "on-device", "蒸餾", "distill"],
     "checks": "知道量化（用位數換記憶體）、持續批次、推測解碼、蒸餾各自的取捨，裝置端要考慮模型大小、電量、離線",
     "questions": ["設計一個在手機上跑的 AI 助理"],
     "probes": ["量化到 4 位元會損失什麼？怎麼驗證？", "哪些請求在裝置上跑、哪些送雲端？", "推測解碼在什麼情況下沒效？"],
     "pitfalls": "只講技術名詞不講取捨；沒驗證最佳化後的品質"},
    {"key": "multimodal", "name": "語音與多模態",
     "kw": ["語音", "voice", "speech", "stt", "tts", "asr", "影像", "圖片", "image", "vision", "multimodal", "多模態", "ocr", "影片", "video"],
     "checks": "語音要把轉文字、模型、合成都串流並重疊，算整段延遲預算；圖片等媒體存物件儲存、注意 token 成本",
     "questions": ["設計一個即時語音客服 agent", "使用者上傳發票照片自動記帳，系統怎麼設計？"],
     "probes": ["從使用者說完到聽到回應，各步驟各花多少時間？", "使用者插話時怎麼處理？", "高解析度圖片很貴，怎麼控制成本？"],
     "pitfalls": "語音各步驟串行等待；沒有延遲預算"},
    {"key": "structured", "name": "結構化輸出與 Prompt 管理",
     "kw": ["json", "structured", "結構化", "prompt", "提示詞", "dspy", "schema"],
     "checks": "知道用 JSON Schema 或受限解碼確保格式，prompt 要像程式碼一樣做版本、測試與逐步上線",
     "questions": ["模型偶爾回傳格式壞掉的 JSON，下游程式會壞，你怎麼處理？"],
     "probes": ["除了重試，還有什麼方法保證格式？", "prompt 改版怎麼上線才安全？"],
     "pitfalls": "靠正規表示式修 JSON；prompt 改了不測就上線"},
    {"key": "tenancy", "name": "多租戶與資料隔離",
     "kw": ["多租戶", "tenant", "saas", "b2b", "企業客戶", "enterprise"],
     "checks": "每個租戶有自己的配額、成本追蹤和資料隔離，共用快取或索引時要防止資料外洩",
     "questions": ["你的 AI 功能要賣給 100 家企業客戶，資料和成本怎麼隔離？"],
     "probes": ["向量索引要每家一份還是共用？", "一家客戶用量暴增時，怎麼不影響別家？"],
     "pitfalls": "共用語意快取或索引導致跨租戶外洩"},
]
DEFAULT_KEYS = ["rag", "estimate", "evaluation", "safety"]       # 職缺沒寫細節時，AI 系統設計最常考的幾個

# 判斷是不是 AI 工程職缺：職稱或內容出現這些字（小寫）
_AI_STRONG = ["llm", "大型語言模型", "語言模型", "生成式", "genai", "gen ai", "generative", "rag", "machine learning", "機器學習",
              "deep learning", "深度學習", "mlops", "nlp", "自然語言", "prompt engineer", "langchain", "llamaindex", "向量資料庫",
              "ai engineer", "ai 工程", "ai工程", "ml engineer", "ai agent", "ai 應用", "人工智慧", "computer vision", "電腦視覺"]
_AI_WORD = re.compile(r"(?<![a-z])(ai|ml|llm|rag|agent)(?![a-z])")


def _job_text(job: dict) -> tuple[str, str]:
    title = (job.get("title") or "").lower()
    parts = [job.get(k) or "" for k in ("about", "summary", "description")]
    parts += [x for k in ("duties", "requirements", "tags") for x in (job.get(k) or [])]
    return title, " ".join(str(p) for p in parts).lower()


def is_ai_job(job: dict | None) -> bool:
    if not job:
        return False
    title, body = _job_text(job)
    score = sum(2 for k in _AI_STRONG if k in title) + sum(1 for k in _AI_STRONG if k in body)
    score += 2 * len(_AI_WORD.findall(title)) + min(1, len(_AI_WORD.findall(body)))     # 內文的「AI」可能只是公司名稱，最多算 1 分
    return score >= 2


def pick_topics(job: dict, n: int = 4) -> list[dict]:
    """依職缺文字挑最相關的 n 個主題；命中太少時用最常考的主題補滿。"""
    title, body = _job_text(job)
    text = f"{title} {body}"
    scored = sorted(((sum(1 for k in t["kw"] if k in text), i, t) for i, t in enumerate(TOPICS)), key=lambda x: (-x[0], x[1]))
    chosen = [t for s, _, t in scored if s > 0][:n]
    for k in DEFAULT_KEYS:
        if len(chosen) >= n:
            break
        t = next(x for x in TOPICS if x["key"] == k)
        if t not in chosen:
            chosen.append(t)
    return chosen


def context(job: dict | None, n: int = 4) -> dict | None:
    """給出題、面試官簡報、面試建議用：不是 AI 職缺時回傳 None。"""
    if not is_ai_job(job):
        return None
    return {
        "說明": "這是 AI／LLM 工程職缺。系統設計題要像真的 AI 系統設計面試：換成這家公司的產品情境、給具體規模（使用者量、延遲、預算），"
                "不要照抄下面的題目；追問方向和常見錯誤只是參考。",
        "解題步驟": FRAMEWORK,
        "相關主題": [{"主題": t["name"], "面試官看什麼": t["checks"], "常見題型": t["questions"],
                    "追問方向": t["probes"], "常見錯誤": t["pitfalls"]} for t in pick_topics(job, n)],
    }


def probes(job: dict | None, n: int = 2) -> list[str] | None:
    """給即時追問用（要 3 秒內回應，只給最相關主題的追問方向）。"""
    if not is_ai_job(job):
        return None
    return [p for t in pick_topics(job, n) for p in t["probes"]]


def eval_guide(job: dict | None, category: str | None) -> dict | None:
    """給評分用：AI 職缺的系統設計題，對照解題步驟看候選人缺了哪一步。"""
    if category != "系統設計" or not is_ai_job(job):
        return None
    return {"解題步驟": FRAMEWORK, "相關主題重點": [t["checks"] for t in pick_topics(job, 3)]}
