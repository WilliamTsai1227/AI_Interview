#!/usr/bin/env bash
# 部署到 Lima VM 裡的 k3s：build image → 匯入 k3s → 套用設定 → 等 migration 和滾動更新完成
#   用法（在專案根目錄）：./deploy/k3s/deploy.sh
#   需要：KUBECONFIG 指向 k3s（見 deploy/k3s/README.md）。Lima VM 名稱預設 k3s，不同時用 LIMA_VM=名稱 指定
set -euo pipefail
cd "$(dirname "$0")/../.."

VM=${LIMA_VM:-k3s}
NS=ai-interview
# tag 用 git commit：K8s 看到 tag 變了才會滾動更新。有未提交的修改時加上時間，每次部署都是新 tag
TAG=$(git rev-parse --short HEAD)
git diff --quiet HEAD -- backend frontend || TAG="$TAG-dirty-$(date +%s)"
IMAGE=ai-interview-app:$TAG

echo "==> 部署到 $(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"

echo "==> build $IMAGE"
docker build -f backend/Dockerfile -t "$IMAGE" .

echo "==> 匯入 k3s（Lima VM：${VM}）"
# image 不在任何 registry 上，所以直接匯入 k3s 自己的 containerd。上雲端時這一步改成 docker push 到 registry
docker save "$IMAGE" | limactl shell "$VM" sudo k3s ctr images import -

echo "==> 套用設定"
kubectl apply -f deploy/k3s/traefik-config.yaml   # Traefik 改用 8009 埠（在 kube-system，不在 kustomization 裡）
kubectl apply -f deploy/k3s/namespace.yaml
secret_changed=false
if [[ -f backend/.env ]]; then
  # backend/.env 的金鑰放進 Secret，並記下檔案的雜湊值：之後 .env 有改才重啟 Pod（Pod 只在啟動時讀環境變數）
  sha=$(shasum -a 256 backend/.env | cut -c1-16)
  old=$(kubectl get secret app-secrets -n $NS -o jsonpath='{.metadata.annotations.env-sha}' 2>/dev/null || true)
  if [[ $sha != "$old" ]]; then
    kubectl create secret generic app-secrets -n $NS --from-env-file=backend/.env --dry-run=client -o yaml \
      | kubectl annotate -f - --local env-sha="$sha" -o yaml | kubectl apply -f -
    [[ -n $old ]] && secret_changed=true   # 第一次建立時 Pod 還沒啟動，不用重啟
  fi
fi
kubectl delete job migrate -n $NS --ignore-not-found   # Job 建立後不能修改，刪掉重建才會用新 image 再跑一次
kubectl kustomize deploy/k3s | sed "s#image: ai-interview-app:dev#image: $IMAGE#" | kubectl apply -f -

echo "==> 等 migration 完成"
kubectl wait --for=condition=complete job/migrate -n $NS --timeout=300s

if $secret_changed; then
  kubectl rollout restart deployment/api deployment/worker-interactive deployment/worker-default -n $NS
fi

echo "==> 等滾動更新完成"
for d in api worker-interactive worker-default; do
  kubectl rollout status deployment/$d -n $NS --timeout=300s
done

echo "==> 完成：http://localhost:8009"
