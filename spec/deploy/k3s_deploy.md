# k3s 完整部署指南（Mac ＋ Lima VM）

> 從零開始：在 Mac 上建立 Lima VM（裡面跑 k3s）、安裝 kubectl、讓 Mac 連到 VM 裡的叢集，再把專案打包成 image 部署上去。
>
> 相關文件：[deploy/k3s/README.md](../../deploy/k3s/README.md)（manifest 檔案說明、常用指令、已知限制）・[deploy/k3s/deploy.sh](../../deploy/k3s/deploy.sh)（一鍵部署腳本）

---

## 1. 整體怎麼運作

```
                      Mac
 ┌────────────────────────────────────────────────────────┐
 │  docker（Colima）  ── 打包 image                        │
 │  kubectl           ── 下指令給 k3s                      │
 │  limactl           ── 管理 VM、把 image 送進 VM          │
 │  瀏覽器 → http://localhost:8009                         │
 └───────┬──────────────────┬──────────────────┬──────────┘
         │ ① kubectl        │ ② image          │ ③ 網頁流量
         │ https://         │ docker save      │ localhost:8009
         │ 127.0.0.1:6443   │ │ limactl shell  │ localhost:9100
         ▼                  ▼                  ▼
 ┌────────────────────────────────────────────────────────┐
 │  Lima VM「k3s」（Ubuntu）                               │
 │   k3s：API Server(6443)・containerd・Traefik(8009)      │
 │   Pod：api × 2・worker × 2・db・redis・minio・migrate   │
 └────────────────────────────────────────────────────────┘
```

Mac 和 VM 之間只有三條路：

| # | 用途 | 怎麼通 |
|---|---|---|
| ① | `kubectl` 下指令 | Lima 自動把 VM 的 6443 埠轉到 Mac 的 `127.0.0.1:6443`，`kubeconfig` 指向這裡 |
| ② | 把 image 送進叢集 | `docker save` 輸出，經 `limactl shell`（SSH）送進 VM，再用 `k3s ctr images import` 匯入 |
| ③ | 瀏覽網站 | Lima 自動把 VM 的 8009、9100 埠轉到 Mac 的 localhost |

重點：**kubectl 裝在 Mac，k3s 跑在 VM**，兩邊靠 `kubeconfig` 這個檔案接起來；**image 沒有經過 registry**，是直接匯入 k3s 自己的 containerd。

---

## 2. 一次性準備

### 2.1 安裝工具

| 工具 | 用途 | 安裝 |
|---|---|---|
| Lima | 建立並管理 VM | `brew install lima` |
| Colima＋docker CLI | 在 Mac 上 build image | `brew install colima docker` |
| kubectl | 操作 Kubernetes | 見下方 |

驗證：

```bash
limactl --version
colima version
docker --version
kubectl version --client
```

**kubectl 的兩種安裝方式**

```bash
# 方式 A：brew（最簡單）
brew install kubectl
```

> 如果你的 macOS 是舊版（例如 macOS 14，Homebrew 會警告 Tier 3、不再提供預編譯套件），`brew install kubectl` 會從原始碼編譯 go、cmake 等一長串依賴，可能要跑一小時以上。這時用方式 B。

```bash
# 方式 B：直接下載官方編好的執行檔（約 1 分鐘，不經過 brew）
uname -m                       # x86_64 = Intel（用 amd64）；arm64 = Apple Silicon（把下面的 amd64 改成 arm64）
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/darwin/amd64/kubectl"
chmod +x kubectl
sudo mv kubectl /usr/local/bin/kubectl
kubectl version --client
```

- 兩種方式裝出來的功能相同。之後如果想換成 brew 版，要先 `sudo rm /usr/local/bin/kubectl`，否則會連結衝突。
- kubectl 與 k3s 的版本差一個小版本以內都能用。

### 2.2 啟動 Colima（docker 的後端）

Mac 沒有原生 Docker，`docker build` 需要 Colima 提供 Docker daemon：

```bash
colima start
docker ps                      # 能列出（空的也行）就代表正常
```

### 2.3 建立 Lima VM 並安裝 k3s

Lima 內建 k3s 範本，一行就會建立 Ubuntu VM、裝好 k3s：

```bash
limactl start --name=k3s --cpus=2 --memory=4 template://k3s
```

- `--memory=4` 單位是 GiB。這個專案有 PostgreSQL、Redis、MinIO、api、兩個 worker，建議至少 4 GiB。
- 第一次要下載 Ubuntu 映像檔和 k3s，需要幾分鐘。
- 過程中如果問要不要編輯設定，直接選繼續。

確認 VM 在跑：

```bash
limactl list
# NAME   STATUS    SSH                CPUS  MEMORY  DISK
# k3s    Running   127.0.0.1:xxxxx    2     4GiB    100GiB
```

進 VM 看看 k3s：

```bash
limactl shell k3s                          # 進入 VM（exit 離開）
limactl shell k3s sudo k3s kubectl get nodes   # 不進去，直接下指令
```

### 2.4 讓 Mac 的 kubectl 連到 VM 裡的 k3s（KUBECONFIG）

`kubeconfig` 是一個 YAML 檔，記錄叢集位址、憑證和使用者。k3s 把它放在 VM 的 `/etc/rancher/k3s/k3s.yaml`，我們把它複製到 Mac：

```bash
mkdir -p ~/.kube
limactl shell k3s sudo cat /etc/rancher/k3s/k3s.yaml > ~/.kube/k3s.yaml
chmod 600 ~/.kube/k3s.yaml          # 裡面有憑證，只讓自己讀
```

指定 kubectl 用這個檔案：

```bash
export KUBECONFIG=~/.kube/k3s.yaml
kubectl get nodes
# NAME       STATUS   ROLES           AGE   VERSION
# lima-k3s   Ready    control-plane   ...   v1.xx.x+k3s1
```

**為什麼這樣就連得到？** 看檔案裡的 `server:`：

```bash
grep server ~/.kube/k3s.yaml
#     server: https://127.0.0.1:6443
```

k3s 的 API Server 在 VM 裡聽 6443 埠，Lima 偵測到後自動轉到 Mac 的 `127.0.0.1:6443`，所以 Mac 上的 kubectl 連 `127.0.0.1` 就等於連到 VM。k3s 的憑證本來就包含 `127.0.0.1`，不用改任何東西。

**讓設定長久有效**

`export` 只對目前的終端機有效。想每個新終端機都生效：

```bash
echo 'export KUBECONFIG=~/.kube/k3s.yaml' >> ~/.zshrc
source ~/.zshrc
```

> 如果你還有別的叢集（例如 Docker Desktop、雲端 EKS），不想互相蓋掉，就維持「每次部署前才 `export`」，或用 `kubectl config view --flatten` 合併成同一個 `~/.kube/config`。`deploy.sh` 開頭會印出它要部署的叢集位址（`==> 部署到 https://127.0.0.1:6443`），部署前看一眼，確認不是連錯叢集。

---

## 3. 把專案打包成 image 放進 VM

### 3.1 為什麼要特別做這一步

Kubernetes 的 Pod 是從 **image** 啟動的，正常流程是 `docker push` 到 registry（Docker Hub、ECR 等），叢集再 pull 下來。本機練習不想架 registry，所以改成：

1. 在 Mac 用 Docker build 出 image。
2. 把 image 打包成 tar，直接塞進 VM 裡 k3s 的 containerd。
3. manifest 用 `imagePullPolicy: IfNotPresent`：叢集發現本機已有這個 image，就不會去外面下載。

### 3.2 手動做一次（理解原理）

在專案根目錄：

```bash
# 1. build（Mac 上，用 Colima 的 Docker）
docker build -f backend/Dockerfile -t ai-interview-app:manual .

# 2. 匯入 k3s：docker save 輸出 tar → 經 SSH 傳進 VM → ctr 匯入
docker save ai-interview-app:manual | limactl shell k3s sudo k3s ctr images import -

# 3. 確認 VM 裡有這個 image
limactl shell k3s sudo k3s ctr images ls | grep ai-interview
```

- `docker save` 把 image 輸出到標準輸出；`limactl shell k3s ...` 把這串資料交給 VM 裡的指令；最後的 `-` 代表從標準輸入讀取。
- **k3s 用 containerd，不是 Docker**，所以 VM 裡看不到 `docker images`，要用 `k3s ctr images ls`。
- Mac 的架構（Intel 是 x86_64、Apple Silicon 是 arm64）要跟 VM 一致。Lima 預設建立跟你 Mac 同架構的 VM，Colima 也是，所以 build 出來的 image 可以直接用。

### 3.3 每次部署都用不同的 tag

K8s 只在 manifest 的 image 字串改變時才會滾動更新。如果每次都叫 `ai-interview-app:latest`，改了程式碼 Pod 也不會換。`deploy.sh` 的做法是用 **git commit 當 tag**，有未提交的修改就加上時間：

```
ai-interview-app:6996397                  ← 乾淨的 commit
ai-interview-app:6996397-dirty-1731312000 ← 有未提交的修改
```

---

## 4. 部署

### 4.1 部署前檢查

```bash
colima status                       # Docker 有在跑
limactl list                        # k3s 是 Running
export KUBECONFIG=~/.kube/k3s.yaml
kubectl get nodes                   # lima-k3s Ready
```

另外，**docker compose 和 k3s 用同樣的連接埠（8009、9100），不能同時開**。先 `docker compose down`。

### 4.2 設定金鑰（選用）

要用 OpenAI 或 Google 登入才需要：

```bash
cp backend/.env.example backend/.env
# 編輯 backend/.env，填入 OPENAI_API_KEY 等金鑰
```

沒有 `backend/.env` 也能部署，會是開發模式（用假資料）。每個變數的意思見 [README.md](../../README.md) 的〈環境變數〉。`deploy.sh` 會把這個檔案轉成 K8s 的 Secret `app-secrets`。

> `DATABASE_URL`、`REDIS_URL`、`S3_*`、`BASE_URL` 這幾個連線位址在 k3s 裡由 [config.yaml](../../deploy/k3s/config.yaml) 決定，寫在 `.env` 不會生效。

### 4.3 一鍵部署

```bash
./deploy/k3s/deploy.sh
```

腳本依序做這些事：

| 步驟 | 做什麼 |
|---|---|
| 1 | `docker build`，tag 用 git commit |
| 2 | `docker save \| limactl shell k3s sudo k3s ctr images import -`，image 進 k3s |
| 3 | 把 `backend/.env` 建成 Secret（有改才更新） |
| 4 | 套用 Traefik 設定（對外埠改成 8009），刪掉舊的 `migrate` Job，再用 `kubectl apply` 套用全部 manifest |
| 5 | 等 `migrate` 跑完（建資料表） |
| 6 | 等 api、worker 滾動更新完成 |

第一次要下載 PostgreSQL、Redis、MinIO 的 image，約幾分鐘。看到 `==> 完成：http://localhost:8009` 就好了。

### 4.4 驗證

```bash
kubectl get pods -n ai-interview
```

應該看到全部是 `Running`（`migrate` 是 `Completed`）：

```
NAME                                  READY   STATUS      RESTARTS
api-xxxxxxxxxx-aaaaa                  1/1     Running     0
api-xxxxxxxxxx-bbbbb                  1/1     Running     0
db-0                                  1/1     Running     0
minio-0                               1/1     Running     0
redis-0                               1/1     Running     0
migrate-xxxxx                         0/1     Completed   0
worker-default-xxxxxxxxxx-ccccc       1/1     Running     0
worker-interactive-xxxxxxxxxx-ddddd   1/1     Running     0
```

打開 <http://localhost:8009>，註冊一個帳號就能用。

管理後台（`/#admin`）沒有預設帳號：在 `backend/.env` 設 `ADMIN_GOOGLE_EMAILS`、`ADMIN_CONSOLE_USERNAME`、`ADMIN_CONSOLE_PASSWORD` 後重新部署，用名單內的帳號「以 Google 登入」，側欄就會出現「管理後台」，再輸入後台帳密即可進入。

---

## 5. 日常操作

### 5.1 改了程式碼，重新部署

```bash
./deploy/k3s/deploy.sh
```

同一個指令。api 有 2 個 Pod，會**先開新的、就緒後才關舊的**，不會停機。

### 5.2 看狀態和 log

```bash
kubectl get pods -n ai-interview
kubectl logs -n ai-interview deploy/api -f
kubectl logs -n ai-interview -l app=worker -f
kubectl describe pod -n ai-interview <Pod 名稱>      # 卡住時看最下面的 Events
```

### 5.3 回到上一版

```bash
kubectl rollout undo deployment/api -n ai-interview
```

### 5.4 開機、關機

```bash
limactl stop k3s        # 關 VM（資料保留）
limactl start k3s       # 再開，Pod 會自動回來
colima stop             # 不 build 時可以關掉省資源
```

Mac 重開機後：`colima start`、`limactl start k3s`，再 `export KUBECONFIG=...`。

### 5.5 切回 docker compose

```bash
limactl stop k3s
docker compose up -d
```

---

## 6. 常見問題

| 現象 | 原因與解法 |
|---|---|
| `zsh: command not found: kubectl` | 還沒裝 kubectl，見 2.1。裝完開新終端機。 |
| `kubectl get nodes` 回 `connection refused`／`The connection to the server localhost:8080 was refused` | 沒設 `KUBECONFIG`（會退回預設的 8080）。執行 `export KUBECONFIG=~/.kube/k3s.yaml`。 |
| `dial tcp 127.0.0.1:6443: connect: connection refused` | VM 沒開。`limactl list`，不是 Running 就 `limactl start k3s`；剛啟動時等約 30 秒。 |
| `kubectl` 連到錯的叢集 | `echo $KUBECONFIG`、`kubectl config current-context`。`deploy.sh` 開頭會印出目標位址。 |
| `Cannot connect to the Docker daemon` | Colima 沒開，`colima start`。 |
| Pod 狀態 `ErrImagePull`／`ImagePullBackOff` | image 沒有匯入 k3s，或 tag 對不上。`limactl shell k3s sudo k3s ctr images ls \| grep ai-interview` 看有沒有；沒有就重跑 `deploy.sh`。 |
| Pod 狀態 `exec format error` | image 與 VM 的 CPU 架構不同（x86_64 vs arm64）。確認 `uname -m`、VM 內 `limactl shell k3s uname -m` 相同。 |
| `localhost:8009` 打不開 | 先確認 docker compose 沒開（同一個埠）：`docker compose ps`。再看 `kubectl get pods -n ai-interview` 和 `kubectl get ingress -n ai-interview`。 |
| `migrate` 一直失敗 | `kubectl logs -n ai-interview job/migrate`。通常是 `db-0` 還沒就緒，等一下重跑 `deploy.sh`。 |
| 改了 `backend/.env` 沒生效 | 重跑 `deploy.sh`，它偵測到 `.env` 變動才會重啟 Pod（Pod 只在啟動時讀環境變數）。 |
| `brew install kubectl` 跑很久 | macOS 版本太舊沒有預編譯套件，改用 2.1 的方式 B。 |

---

## 7. 清除

```bash
kubectl delete namespace ai-interview        # 刪掉專案（含資料庫、Redis、MinIO 資料）
limactl stop k3s                             # 只停 VM，保留所有資料
limactl delete k3s                           # 連 VM 一起刪掉（k3s 要重裝）
rm ~/.kube/k3s.yaml                          # 不再使用時刪掉 kubeconfig
```

---

## 8. 指令速查

| 目的 | 指令 |
|---|---|
| 建 VM 並裝 k3s | `limactl start --name=k3s --cpus=2 --memory=4 template://k3s` |
| 取得 kubeconfig | `limactl shell k3s sudo cat /etc/rancher/k3s/k3s.yaml > ~/.kube/k3s.yaml` |
| 讓 kubectl 連 k3s | `export KUBECONFIG=~/.kube/k3s.yaml` |
| 確認連線 | `kubectl get nodes` |
| build image | `docker build -f backend/Dockerfile -t ai-interview-app:TAG .` |
| 送 image 進 VM | `docker save ai-interview-app:TAG \| limactl shell k3s sudo k3s ctr images import -` |
| 看 VM 裡的 image | `limactl shell k3s sudo k3s ctr images ls` |
| 一鍵部署 | `./deploy/k3s/deploy.sh` |
| 看 Pod | `kubectl get pods -n ai-interview` |
| 進 VM | `limactl shell k3s` |
| 不裝 kubectl 的替代 | `limactl shell k3s sudo k3s kubectl get nodes` |
