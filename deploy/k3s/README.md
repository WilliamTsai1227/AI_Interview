# k3s 部署（本機 Lima VM）

> 這個資料夾是**用 k3s（輕量版 Kubernetes）部署**的設定，跟 [docker-compose.yml](../../docker-compose.yml) 是兩套獨立的部署方式，同一份程式、同一個 image。兩邊可以同時開，連接埠不衝突。

## 架構

```
Mac 瀏覽器
  │  http://localhost        → Traefik（k3s 內建 Ingress）→ Service api → api Pod × 2
  │  http://localhost:9200   → Service minio-public（LoadBalancer）→ minio-0   （預簽名網址直接上傳）
  │
  └─ Lima 把 VM 的 80、9200 埠轉到 Mac 的 localhost

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

完成後打開 <http://localhost>。第一次要下載 PostgreSQL、Redis、MinIO 的 image，約需幾分鐘。

deploy.sh 做的事：

1. 用 git commit 當 tag build image（例如 `ai-interview-app:6996397`）。有未提交的修改時加上時間，確保每次都是新 tag，K8s 才會更新。
2. `docker save | k3s ctr images import`：把 image 直接匯入 k3s。image 不在任何 registry 上，所以 manifest 設了 `imagePullPolicy: IfNotPresent`。
3. 把 `backend/.env` 建成 Secret `app-secrets`（OpenAI、Google、JWT 等金鑰）。`.env` 有改才會重啟 Pod。
4. 刪掉舊的 migrate Job 再套用全部設定：Job 建立後不能修改，所以每次都重建。
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
kubectl exec -n ai-interview deploy/api -- python -m app.seed --admin demo@example.com   # 建示範帳號

kubectl scale deployment/worker-interactive -n ai-interview --replicas=3  # 面試的背景工作加到 3 個
kubectl rollout undo deployment/api -n ai-interview                       # api 回到上一版
```

MinIO 主控台沒有對外開放，要看時：`kubectl port-forward -n ai-interview svc/minio 9201:9001`，打開 <http://localhost:9201>（帳密 `minioadmin`）。

## 刪除

```bash
kubectl delete namespace ai-interview      # 全部刪除，包含資料庫、Redis、MinIO 的資料
```

只想停止但保留資料：`limactl stop k3s`，之後 `limactl start k3s`，Pod 會自動回來。

## 已知限制

- **worker 被替換時，進行中的工作會中斷**：[backend/app/workers/main.py](../../backend/app/workers/main.py) 設了 `handle_signals=False`，也沒有自己處理 SIGTERM，所以 Pod 收到關閉訊號就直接結束。工作不會遺失，但要等 `sweep` 排程在 10 分鐘後重新投遞。滾動更新 worker 時，正在面試的使用者可能要多等評分結果。
- **密碼寫在 Git 裡**：[config.yaml](config.yaml) 的帳密是練習用，跟 docker-compose.yml 相同。正式環境要改用 `kubectl create secret`、Sealed Secrets 或雲端的 Key Vault。
- **單節點**：資料庫、Redis、MinIO 都只有 1 份，磁碟在 VM 本機。VM 刪掉資料就沒了。
- **只有 HTTP**：`BASE_URL` 是 `http://localhost`，`COOKIE_SECURE` 維持 `false`。對外開放時要加 cert-manager 申請 HTTPS 憑證。
