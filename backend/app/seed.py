"""開發用種子資料：範例平台職缺（虛構公司，原始網址為 example.com）、示範帳號、管理員。

用法：python -m app.seed [--admin email]
"""
import asyncio
import sys

from app.core import db
from app.core.security import hash_password
from app.modules.jobs.service import search_text

DEMO_JOBS = [
    dict(ref="northwind-sfe", company="Northwind Labs", title="Senior Frontend Engineer", city="台北市", loc="台北市信義區",
         mode="hybrid", etype="全職", pay="月薪 75,000 – 95,000", cat="frontend", sen="senior", tags=["React", "TypeScript", "設計系統"],
         summary="總部在新加坡的餐飲 SaaS 公司，台北是亞太研發中心。負責 POS 後台與店家 App 的前端架構。",
         duties=["主導後台前端架構與效能優化", "與設計師共建元件庫", "帶領 2 位初階工程師做 Code Review"],
         reqs=["3 年以上 React 經驗", "熟悉 TypeScript 與狀態管理", "有設計系統經驗佳"], foreign=True, lang="zh"),
    dict(ref="bluepeak-fe-payments", company="Bluepeak Pay", title="Frontend Engineer, Payments", city="台北市", loc="台北市中山區",
         mode="hybrid", etype="全職", pay="年薪 120 – 150 萬", cat="frontend", sen="mid", tags=["Vue", "Web 安全", "測試"],
         summary="美系支付公司的台灣團隊，負責亞洲市場的商家產品，工作語言為英文。",
         duties=["開發收款與對帳介面", "撰寫單元與 E2E 測試", "和後端一起定義 API 規格"],
         reqs=["2 年以上前端經驗", "了解 XSS、CSRF 等前端資安", "英文口說流利"], foreign=True, lang="en"),
    dict(ref="lumen-fe", company="Lumen Travel", title="Frontend Engineer", city="台中市", loc="台中市西屯區", mode="onsite",
         etype="全職", pay="月薪 58,000 – 72,000", cat="frontend", sen="mid", tags=["Next.js", "SEO", "i18n"],
         summary="日系線上旅遊平台的台灣分公司，正在改版行程預訂流程。",
         duties=["維護 Next.js 官網與預訂流程", "改善 Core Web Vitals", "多語系內容管理"],
         reqs=["熟悉 SSR / SSG", "有 SEO 優化經驗", "英文可作為工作語言"], foreign=True, lang="zh"),
    dict(ref="cloudnine-pfe", company="Cloudnine Health", title="Product Frontend Engineer", city="新竹市", loc="新竹市東區",
         mode="remote", etype="全職", pay="月薪 70,000 – 88,000", cat="mobile", sen="mid", tags=["React Native", "資料視覺化", "A/B 測試"],
         summary="慢性病照護 App，和醫院合作，重視無障礙與長輩友善設計。",
         duties=["開發病患端 App 與照護儀表板", "與 PM 一起設計實驗", "推動無障礙標準"],
         reqs=["React 或 React Native 經驗", "對產品數據有興趣", "重視使用者體驗"], foreign=False, lang="zh"),
    dict(ref="harbor-be", company="Harbor Logistics", title="Backend Engineer", city="桃園市", loc="桃園市龜山區", mode="onsite",
         etype="全職", pay="年薪 110 – 140 萬", cat="backend", sen="senior", tags=["Python", "PostgreSQL", "Kafka"],
         summary="跨境物流平台，後端團隊負責訂單與追蹤系統，正在拆分單體服務。",
         duties=["設計訂單與追蹤服務 API", "優化 PostgreSQL 查詢與資料模型", "建立事件驅動架構"],
         reqs=["4 年以上後端經驗", "熟悉 Python 或 Go", "有訊息佇列經驗"], foreign=False, lang="zh"),
    dict(ref="atelier-ui", company="Atelier Nine", title="UI Engineer", city="台北市", loc="台北市大安區", mode="hybrid",
         etype="全職", pay="月薪 60,000 – 75,000", cat="frontend", sen="junior", tags=["CSS", "動態效果", "Figma"],
         summary="品牌設計工作室，替客戶打造形象網站與互動體驗。",
         duties=["把設計稿轉成精緻的互動網頁", "製作微互動與動畫", "維護內部樣式指南"],
         reqs=["CSS 功力扎實", "熟悉 GSAP 或 Framer Motion", "有作品集"], foreign=False, lang="zh"),
]


async def seed(admin_email: str | None) -> None:
    await db.init_pool()
    async with db.transaction() as conn:
        for j in DEMO_JOBS:
            cat = await conn.fetchval("SELECT id FROM job_categories WHERE code = $1", j["cat"])
            url = f"https://example.com/jobs/{j['ref']}"
            await conn.execute(
                """
                INSERT INTO job_posts (source, external_ref, company_name, title, city, location_text, work_mode, employment_type,
                       salary_text, tags, summary, about, duties, requirements, category_id, seniority, source_site, source_url,
                       source_url_norm, is_foreign, work_language, fetched_at, last_verified_at, posted_at, search_text)
                VALUES ('catalog',$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10,$11,$12,$13,$14,'demo',$15,$15,$16,$17, now(), now(),
                        now() - (random() * interval '7 days'), $18)
                ON CONFLICT (source_site, external_ref) WHERE external_ref IS NOT NULL DO NOTHING
                """, j["ref"], j["company"], j["title"], j["city"], j["loc"], j["mode"], j["etype"], j["pay"], j["tags"],
                j["summary"], j["duties"], j["reqs"], cat, j["sen"], url, j["foreign"], j["lang"],
                search_text(j["company"], j["title"], j["tags"]))
        await conn.execute(
            """INSERT INTO users (email, password_hash, display_name) VALUES ('demo@example.com', $1, '陳品妤')
               ON CONFLICT (email) DO NOTHING""", hash_password("demo1234"))
        if admin_email:
            await conn.execute("UPDATE users SET role = 'admin' WHERE email = $1", admin_email)
    await db.close_pool()
    print(f"seeded {len(DEMO_JOBS)} demo jobs; demo user demo@example.com / demo1234"
          + (f"; admin = {admin_email}" if admin_email else ""))


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(seed(args[args.index("--admin") + 1] if "--admin" in args else None))
