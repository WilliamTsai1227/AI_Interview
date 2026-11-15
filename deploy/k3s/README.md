# k3s 部署（本機 Lima VM）

> 這個資料夾是**用 k3s（輕量版 Kubernetes）部署**的設定，跟 [docker-compose.yml](../../docker-compose.yml) 是兩套獨立的部署方式，同一份程式、同一個 image。
>
> **網址跟 docker compose 完全相同**（網站 `localhost:8009`、MinIO `localhost:9100`），所以 Google 登入的重新導向 URI 不用另外設定。也因為連接埠相同，**兩邊不能同時開**：切換前先 `docker compose down`，或 `limactl stop k3s`。

## 架構

```
Mac 瀏覽器
  │  http://localhost:8009   → Traefik（k3s 內建 Ingress）→ Service api → api Pod × 2
  │  http://localhost:9100   → Service minio-public（LoadBalancer）→ minio-0   （預簽名網址直接上傳）
  │
  └─ Lima 把 VM 的 8009、9100 埠轉到 Mac 的 localhost

命名空間 ai-interview
  ├─ api                  Deployment × 2   滾動更新：先開新的、就緒後才關舊的
  ├─ worker-interactive   Deployment × 1   面試中的工作（TTS、轉錄、評分）
  ├─ worker-default       Deployment × 1   解析、出題、報告＋排程（只能 1 個）
  ├─ migrate              Job              建資料表，每次部署都重跑（已執行過的會跳過）
  ├─ db / redis / minio   StatefulSet × 1  各自掛一塊 local-path 磁碟（PVC）
  └─ app-config、infra-credentials、app-secrets   設定與密碼
```

| 檔案 | 內容 | 對應 docker-compose.yml |
|---|---|---|
| [traefik-config.yaml](traefik-config.yaml) | Traefik 對外埠從 80 改成 8009（整個叢集的設定，deploy.sh 另外套用） | `api` 的 `ports: 8009` |
| [kustomization.yaml](kustomization.yaml) | 列出所有資源，`kubectl apply -k` 一次套用 | — |
| [namespace.yaml](namespace.yaml) | 命名空間 `ai-interview` | compose 的專案名稱 |
| [config.yaml](config.yaml) | ConfigMap（連線位址）、Secret（資料庫和 MinIO 帳密） | `x-app.environment` |
| [postgres.yaml](postgres.yaml)、[redis.yaml](redis.yaml)、[minio.yaml](minio.yaml) | 基礎設施 | `db`、`redis`、`minio` |
| [migrate-job.yaml](migrate-job.yaml) | 建資料表 | `migrate` |
| [api.yaml](api.yaml) | API Deployment、Service、Ingress | `api` |
| [worker.yaml](worker.yaml) | 兩條佇列各一個 Deployment | `worker`（compose 是一個程序跑兩條佇列） |
| [deploy.sh](deploy.sh) | build → 匯入 k3s → 套用 → 等完成 | `docker compose up -d --build` |

## 前置條件

1. Lima VM `k3s` 已經裝好 k3s，而且 Mac 上的 kubectl 連得到：

   ```bash
   export KUBECONFIG=~/.kube/k3s.yaml
   kubectl get nodes          # lima-k3s   Ready
   ```

   還沒設定的話：`limactl shell k3s sudo cat /etc/rancher/k3s/k3s.yaml > ~/.kube/k3s.yaml`

2. Docker（Colima）有在跑：deploy.sh 用它 build image。

## 部署

在專案根目錄：

```bash
export KUBECONFIG=~/.kube/k3s.yaml
./deploy/k3s/deploy.sh
```

完成後打開 <http://localhost:8009>。第一次要下載 PostgreSQL、Redis、MinIO 的 image，約需幾分鐘。

deploy.sh 做的事：

1. 用 git commit 當 tag build image（例如 `ai-interview-app:6996397`）。有未提交的修改時加上時間，確保每次都是新 tag，K8s 才會更新。
2. `docker save | k3s ctr images import`：把 image 直接匯入 k3s。image 不在任何 registry 上，所以 manifest 設了 `imagePullPolicy: IfNotPresent`。
3. 把 `backend/.env` 建成 Secret `app-secrets`（OpenAI、Google、JWT 等金鑰）。`.env` 有改才會重啟 Pod。
4. 套用 Traefik 的連接埠設定；刪掉舊的 migrate Job 再套用全部設定：Job 建立後不能修改，所以每次都重建。
5. 等 migration 完成，再等 api 和 worker 滾動更新完成。

改了程式碼就再跑一次 deploy.sh，api 會逐一換成新版本，不會停機。

> **`backend/.env` 裡的連線位址不會生效**：`DATABASE_URL`、`REDIS_URL`、`S3_*`、`BASE_URL` 由 [config.yaml](config.yaml) 指定，叢集內一律用 Service 名稱（`db`、`redis`、`minio`）互連。跟 docker compose 的「Docker 固定」變數是同一個概念。

## 常用指令

```bash
kubectl get pods -n ai-interview                         # 所有 Pod 的狀態
kubectl logs -n ai-interview deploy/api -f               # 看 api 的 log（有 2 個 Pod 時會挑一個）
kubectl logs -n ai-interview -l app=worker -f            # 兩種 worker 的 log 一起看
kubectl describe pod -n ai-interview <名稱>               # 卡住時看最下面的 Events

kubectl exec -it -n ai-interview db-0 -- psql -U ai ai_interview          # 進資料庫

kubectl scale deployment/worker-interactive -n ai-interview --replicas=3  # 面試的背景工作加到 3 個
kubectl rollout undo deployment/api -n ai-interview                       # api 回到上一版
```

MinIO 主控台沒有對外開放，要看時：`kubectl port-forward -n ai-interview svc/minio 9201:9001`，打開 <http://localhost:9201>（帳密 `minioadmin`）。

## 刪除

```bash
kubectl delete namespace ai-interview      # 全部刪除，包含資料庫、Redis、MinIO 的資料
kubectl delete -f deploy/k3s/traefik-config.yaml   # Traefik 改回 80 埠（選用）
```

只想停止但保留資料：`limactl stop k3s`，之後 `limactl start k3s`，Pod 會自動回來。

## 部署不中斷使用者

滾動更新時，每一層各自負責不讓使用者斷線：

| 使用者正在做的事 | 部署時發生什麼 | 靠什麼不中斷 |
|---|---|---|
| 面試中（作答、送出回答） | 舊的 api Pod 先從 Traefik 移除（preStop 5 秒），再等進行中的請求做完（uvicorn 最多 40 秒）才結束 | 面試狀態都在資料庫（`state_version`），換到哪個 Pod 都一樣；送出回答帶 `Idempotency-Key`，前端重送不會重複作答 |
| 上傳履歷、錄音 | 檔案用預簽名網址直接傳到 MinIO／S3，**不經過 api Pod** | 前端 PUT 失敗最多重試 4 次，網址過期就重拿上傳單（[api.js](../../frontend/js/api.js) `uploadFile`） |
| 等評分、等報告 | 舊的 worker Pod 不再接新工作，把手上的做完（最多 job_timeout 秒）；新 Pod 同時已經在接新工作 | 時間到還沒做完的工作**交還**（不扣嘗試次數、立刻重新投遞）；worker 被硬殺時，心跳 30 秒後過期，sweep 收回重排。handler 都可以安全重跑：已經評分完的不會再呼叫 AI |
| 看報告進度（SSE） | 長連線在 api Pod 關閉時被切斷 | 前端 3 秒後自動重連（chat.js、report.js） |
| 登入 | JWT 用同一把金鑰、refresh token 在資料庫、Google 登入的 state 在 Redis | 不依賴是哪個 Pod |
| 其他 API | — | GET 和帶 `Idempotency-Key` 的請求，遇到連線錯誤或 502／503／504 會自動重送 3 次 |

**資料庫 migration 只能「擴充」**：部署時 migration 先跑完，舊版 Pod 還會用新的 schema 跑一陣子。刪欄位、改名、加 NOT NULL 這種會讓舊程式壞掉的變更，要拆成兩次部署（先上不再使用它的程式，下一次才刪）。[test_migration_compat.py](../../backend/tests/test_migration_compat.py) 會擋下沒有寫 `-- deploy-safe: 原因` 的這類 migration。

### 演練：一邊跑流量一邊部署

```bash
export KUBECONFIG=~/.kube/k3s.yaml
backend/.venv/bin/python backend/tests/rollout_drill.py --deploy ./deploy/k3s/deploy.sh --out drill.json
```

同時跑 3 場面試（每題作答 20 秒，橫跨整個部署；macOS 會用 `say` 產生真的語音，STT 和評分才會真的花時間）、3 條上傳迴圈、1 條登入迴圈，送出 2 題回答、評分工作開始跑之後執行部署。結束後等報告評分完成，到資料庫查這段時間的背景工作有沒有 dead，最後把一場面試的轉錄／評分工作改回待重跑，確認分數不變、沒有多呼叫 AI。報表分開列出重送前的原始錯誤和使用者看得到的失敗。開了 OpenAI 金鑰時會真的呼叫 AI。

## 已知限制

- **第一次部署這個版本時**，舊版 worker 還不會平滑關閉：被換掉時手上的工作會中斷，要等 15 分鐘的保底規則收回（舊版 worker 沒有心跳，新版的 sweep 不會提早搶它的工作）。之後的部署就不會再有這個問題。
- **docker compose 做不到不中斷**：compose 是先關舊的再開新的，api 換版時會斷幾秒。要不中斷請用 k3s。
- **密碼寫在 Git 裡**：[config.yaml](config.yaml) 的帳密是練習用，跟 docker-compose.yml 相同。正式環境要改用 `kubectl create secret`、Sealed Secrets 或雲端的 Key Vault。
- **單節點**：資料庫、Redis、MinIO 都只有 1 份，磁碟在 VM 本機。VM 刪掉資料就沒了。
- **只有 HTTP**：`BASE_URL` 是 `http://localhost:8009`，`COOKIE_SECURE` 維持 `false`。對外開放時要加 cert-manager 申請 HTTPS 憑證。
