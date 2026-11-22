"""履歷初篩檢核表：用 HR／招募與用人主管的角度判斷履歷會不會在第一關被刷掉。

用自己的話整理常見的招募實務（台灣 104／新創、外商），只寫「看什麼、什麼是紅旗、怎麼改」，不綁特定公司。
兩種用法：
- rubric(language)：給 prompt 的檢核表（刪減卡、初篩檢查都用同一份，標準才不會每次不一樣）
- rule_checks(doc, jobs)：程式能查準的項目（關鍵字、篇幅、日期、待補、個資），結果交給 AI 一起判斷，也直接列給使用者
"""
import re

HR = [
    "前 6 秒：名字下面那一行（定位）和第一段經歷，要立刻看出「這個人是做什麼的、幾年經驗、跟這個職缺有沒有關」",
    "關鍵字：職缺寫的技能與工具要用同樣的寫法出現（ATS 和 HR 都是比對字面，Postgres 和 PostgreSQL 視為不同）",
    "完整：每段經歷都要有公司、職稱、起訖年月；學歷有學校、科系；聯絡方式有 Email 與電話或 LinkedIn",
    "篇幅：3 年以下一頁；資深兩頁內；超過的部分 HR 通常不會看",
    "紅旗：無說明的空窗（半年以上）、一年內換很多份工作、日期前後矛盾、錯字、同一件事寫兩次、格式不一致",
    "填充詞：「精通 Office」「認真負責」「抗壓性高」「學習能力強」這類每個人都寫的話佔版面又沒有資訊量",
]
MANAGER = [
    "相關性：跟職缺最相關的經歷與專案要排在前面、寫得最詳細，無關的縮短或刪掉",
    "影響力：每條要看得出「做了什麼、怎麼做、結果如何」，最好有數字（規模、速度、金額、使用者數）",
    "角色：看得出是自己主導、負責哪一塊，還是只是「參與」",
    "技術深度：寫出關鍵的技術選擇與原因，不是只列技術名詞",
    "亮點：至少一兩件會讓人想在面試裡追問的事",
    "可信度：技能清單裡的每項，經歷或專案裡最好找得到用過的證據；只列不用會被質疑",
]
PRUNE = [
    "過時的技術或工具（例如 Flash、jQuery 為主、Windows XP），除非職缺明確要求",
    "太久以前、跟目標職缺無關的工作或打工（資深者 10 年以上前的經歷可縮成一行）",
    "重複的內容、同一件事在不同區塊寫兩次",
    "填充詞與自我評價（見 HR 的填充詞）",
    "跟工作無關的個資與敏感資訊（見市場標準）",
    "已經不重要的小作品、課堂作業（有更好的專案時）",
]
MARKET = {
    "zh": [
        "台灣：照片可放可不放；不要放身分證字號、出生年月日、婚姻狀況、血型、家庭成員，這些跟能力無關，還可能被差別對待",
        "台灣：104 等平台習慣寫自傳，但投新創或外商時，一頁式條列履歷比長篇自傳有效",
        "台灣：期望薪資不必寫在履歷上，留到面試談",
    ],
    "en": [
        "外商／英文履歷：不放照片、年齡、生日、婚姻狀況、性別",
        "英文履歷：每條以過去式動詞開頭（Built, Led, Reduced），不用 I；不寫 References available upon request",
        "英文履歷：日期格式一致（Mar 2022 – Present），學歷寫在最後（新鮮人例外）",
    ],
}
VERDICTS = {"pass": "大概率通過", "borderline": "邊緣", "reject": "可能被刷"}


def rubric(language: str) -> str:
    lines = (["【HR／招募看什麼】"] + [f"- {x}" for x in HR] + ["【用人主管看什麼】"] + [f"- {x}" for x in MANAGER]
             + ["【可以刪減的內容】"] + [f"- {x}" for x in PRUNE] + ["【市場標準】"] + [f"- {x}" for x in MARKET.get(language, MARKET["zh"])])
    return "\n".join(lines)


# ---------- 程式檢查 ----------
PERSONAL = [
    (re.compile(r"\b[A-Z][12]\d{8}\b"), "有身分證字號"),
    (re.compile(r"(出生|生日|生於|date of birth|\bDOB\b)", re.I), "有出生日期"),
    (re.compile(r"(婚姻|已婚|未婚|marital)", re.I), "有婚姻狀況"),
    (re.compile(r"(血型|blood type)", re.I), "有血型"),
]
FILLERS = ["精通 office", "精通office", "認真負責", "抗壓性", "學習能力強", "吃苦耐勞", "team player", "hard-working", "hardworking"]
PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,40}\]")
CHARS_PER_PAGE = {"zh": 900, "en": 2600}       # 條列式履歷一頁大約能放的字數（粗估）


def doc_text(doc: dict) -> str:
    out = [doc.get("name") or "", doc.get("headline") or "", doc.get("contact") or ""]
    for s in doc.get("sections") or []:
        out.append(s["title"])
        for e in s["entries"]:
            out += [e["title"], e["meta"], *e["lines"]]
    return "\n".join(x for x in out if x)


def keywords_present(keywords: list[str], text: str) -> list[dict]:
    low = text.lower()
    seen, out = set(), []
    for k in keywords:
        k = k.strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append({"keyword": k[:40], "present": k.lower() in low})
    return out


def rule_checks(doc: dict, *, language: str, length: str) -> list[dict]:
    """回傳 [{severity, title, detail}]：程式查得準、AI 容易漏看的項目。"""
    text = doc_text(doc)
    issues = []
    n = len(PLACEHOLDER.findall(text))
    if n:
        issues.append({"severity": "high", "title": f"還有 {n} 個 [待補] 沒填", "detail": "投遞前要換成實際的數字或內容，不然一看就知道是半成品"})
    if not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", doc.get("contact") or text):
        issues.append({"severity": "high", "title": "沒有 Email", "detail": "HR 看到想聯絡你卻找不到方式，通常就直接跳過"})
    no_date = [e["title"] for s in doc.get("sections") or [] for e in s["entries"]
               if e["title"] and not e["meta"] and re.search(r"經歷|experience|work", s["title"], re.I)]
    if no_date:
        issues.append({"severity": "medium", "title": "有經歷沒寫期間", "detail": f"{'、'.join(no_date[:3])} 沒有起訖年月，HR 無法判斷年資"})
    for pat, what in PERSONAL:
        if pat.search(text):
            issues.append({"severity": "medium", "title": f"{what}", "detail": "跟能力無關的個資，建議拿掉，避免被差別對待"})
    fill = [f for f in FILLERS if f in text.lower()]
    if fill:
        issues.append({"severity": "low", "title": "有填充詞", "detail": f"「{'」「'.join(fill[:3])}」每個人都會寫，換成具體的成果更有說服力"})
    limit = CHARS_PER_PAGE.get(language, 900) * (1 if length == "one" else 2)
    if len(text) > limit * 1.15:
        issues.append({"severity": "medium", "title": "篇幅可能超過" + ("一頁" if length == "one" else "兩頁"),
                       "detail": f"約 {len(text)} 字，建議把跟職缺無關的經歷縮短或刪掉"})
    return issues
