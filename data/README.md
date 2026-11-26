# data/

資料管線相關的獨立程式，不屬於 API 服務本身；各自打包、各自部署，結果寫進主系統的 PostgreSQL。

| 資料夾 | 內容 | 執行環境 |
|---|---|---|
| [job_crawlers/](job_crawlers/) | 職缺爬蟲：讀取招募系統（Greenhouse、Lever、Ashby）的公開職缺 API 與公司官網的 schema.org `JobPosting` 結構化資料，只收台灣職缺，寫入 `job_posts` | AWS Lambda（EventBridge 每日排程） |
| [scheduler/](scheduler/) | 在本機執行上面的爬蟲：讀資料夾內的 `.env`，手動指令或每天定時，寫入 docker compose 的 PostgreSQL | 你的主機 |
