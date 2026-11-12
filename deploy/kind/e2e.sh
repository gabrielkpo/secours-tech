#!/usr/bin/env bash
# End-to-end test of the Helm chart on a kind cluster: the operators it needs, the chart with
# backup on, the migrations (TLS verify-full, non-superuser), an upgrade, and one backup to a
# throwaway S3 endpoint. Run from the repository root; CI runs it on every pull request.
set -euo pipefail

CLUSTER=${CLUSTER:-secours-tech}
NAMESPACE=secours-tech
IMAGE=secours-tech:e2e
# pinned: each at least two weeks old when chosen (2026-11-12)
CERT_MANAGER_CHART=v1.21.3
CNPG_CHART=0.29.1        # operator 1.30.1
BARMAN_CLOUD_CHART=0.8.1 # plugin v0.15.1

step() { printf '\n== %s\n' "$*"; }

if ! kind get clusters | grep -qx "$CLUSTER"; then
  step "kind cluster"
  kind create cluster --name "$CLUSTER" --wait 120s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

step "image"
docker build -t "$IMAGE" .
kind load docker-image "$IMAGE" --name "$CLUSTER"

step "operators"
helm repo add jetstack https://charts.jetstack.io --force-update >/dev/null
helm repo add cnpg https://cloudnative-pg.github.io/charts --force-update >/dev/null
helm upgrade --install cert-manager jetstack/cert-manager --version "$CERT_MANAGER_CHART" \
  --namespace cert-manager --create-namespace --set crds.enabled=true --wait --timeout 5m
helm upgrade --install cnpg cnpg/cloudnative-pg --version "$CNPG_CHART" \
  --namespace cnpg-system --create-namespace --wait --timeout 5m
helm upgrade --install barman-cloud cnpg/plugin-barman-cloud --version "$BARMAN_CLOUD_CHART" \
  --namespace cnpg-system --wait --timeout 5m

step "throwaway S3"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NAMESPACE" apply -f deploy/kind/s3.yaml
kubectl -n "$NAMESPACE" rollout status deployment/s3 --timeout 3m
kubectl -n "$NAMESPACE" exec deploy/s3 -- sh -c 'echo "s3.bucket.create -name backups" | weed shell' >/dev/null
kubectl -n "$NAMESPACE" create secret generic s3-credentials \
  --from-literal=ACCESS_KEY_ID=e2e --from-literal=ACCESS_SECRET_KEY=e2e \
  --dry-run=client -o yaml | kubectl apply -f -

values=(
  --set image.repository=secours-tech --set image.tag=e2e --set image.pullPolicy=Never
  --set database.storage.size=1Gi
  --set backup.enabled=true
  --set backup.destinationPath=s3://backups/secours-tech
  --set backup.endpointURL=http://s3:8333
  --set backup.credentialsSecret=s3-credentials
)

step "chart: install (the migrations hook must succeed)"
helm upgrade --install secours-tech deploy/helm/secours-tech --namespace "$NAMESPACE" \
  "${values[@]}" --wait --timeout 10m

psql() {
  kubectl -n "$NAMESPACE" exec secours-tech-db-1 -c postgres -- psql -d secours_tech -tAc "$1"
}

step "checks"
expected=$(find src/secours_tech/migrations -name '*.sql' | wc -l | tr -d ' ')
applied=$(psql "SELECT count(*) FROM schema_migrations")
[ "$applied" = "$expected" ] || { echo "migrations: $applied applied, $expected expected"; exit 1; }
echo "migrations: $applied of $expected applied"
[ "$(psql "SELECT rolsuper FROM pg_roles WHERE rolname = 'secours_tech'")" = f ]
echo "secours_tech is not a superuser"
[ "$(psql "SELECT count(*) FROM pg_extension WHERE extname IN ('vector', 'unaccent')")" = 2 ]
echo "extensions vector and unaccent present"

step "chart: upgrade (the hook runs again and applies nothing)"
helm upgrade secours-tech deploy/helm/secours-tech --namespace "$NAMESPACE" \
  "${values[@]}" --wait --timeout 10m
kubectl -n "$NAMESPACE" logs job/secours-tech-migrate | grep -q "0 migration(s) applied"
echo "second run applied nothing"

step "backup to S3"
backup="e2e-$(date +%s)"
kubectl -n "$NAMESPACE" apply -f - <<YAML
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: $backup
spec:
  cluster:
    name: secours-tech-db
  method: plugin
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
YAML
phase=""
for _ in $(seq 60); do
  phase=$(kubectl -n "$NAMESPACE" get backups.postgresql.cnpg.io "$backup" -o jsonpath='{.status.phase}')
  case "$phase" in
    completed) echo "backup $backup completed"; break ;;
    failed) kubectl -n "$NAMESPACE" get backups.postgresql.cnpg.io "$backup" -o yaml; exit 1 ;;
  esac
  sleep 5
done
[ "$phase" = completed ] || { echo "backup $backup did not complete: $phase"; exit 1; }
# the scheduled backup is immediate: a base backup is taken right after the install
scheduled=""
for _ in $(seq 60); do
  scheduled=$(kubectl -n "$NAMESPACE" get backups.postgresql.cnpg.io \
    -l cnpg.io/scheduled-backup=secours-tech-db-daily -o jsonpath='{.items[*].status.phase}')
  [[ "$scheduled" == *completed* ]] && break
  sleep 5
done
[[ "$scheduled" == *completed* ]] || { echo "no completed scheduled backup: $scheduled"; exit 1; }
echo "the daily schedule took its first backup at install"

step "all checks passed"
