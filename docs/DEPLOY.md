# Deploying on Kubernetes

The chart `deploy/helm/secours-tech` runs, today, the database and its schema: PostgreSQL 17
with pgvector managed by CloudNativePG, its daily backup outside the cluster, and the SQL
migrations. The API and its identity proxy join it at J5 (D24, D25).

## What the cluster needs first

Installed once, cluster-wide, outside the chart (versions tested by `deploy/kind/e2e.sh`):

| Component | Chart | Why |
|---|---|---|
| cert-manager | `jetstack/cert-manager` v1.21.3 | certificates of the backup plugin |
| CloudNativePG | `cnpg/cloudnative-pg` 0.29.1 (operator 1.30.1) | runs Postgres, its TLS, users and failover |
| barman-cloud plugin | `cnpg/plugin-barman-cloud` 0.8.1 (v0.15.1) | base backups and WAL archive to S3 |

The plugin is needed only with `backup.enabled`.

## What the chart creates

- A CloudNativePG `Cluster` named `<release>-db`: one instance (one VM, one disk, D23), the
  `secours_tech` database owned by the `secours_tech` role, which is not a superuser. Superuser
  access stays disabled. The `vector` and `unaccent` extensions are created once at bootstrap.
- A `Job`, run as a Helm hook after every install and upgrade, that applies the migrations. It
  connects with TLS checked against the cluster's own CA and the server's name
  (`sslmode=verify-full`). Its password comes from the operator's secret. It runs as uid 10001
  with a read-only root filesystem, no capabilities and no Kubernetes API token.
- With `backup.enabled`: a barman-cloud `ObjectStore` (gzip, retention `backup.retention`) and a
  `ScheduledBackup` (03:00 every day, and a first one right after the install). WAL is archived
  continuously, so the database can be restored to any point within the retention.

## Backup to Oracle Object Storage

Oracle's S3-compatible API stores the backups outside the VM (free up to 20 GB) and encrypts
them at rest. Create a bucket and a customer secret key, then the Secret the chart reads (never
committed):

```bash
kubectl -n secours-tech create secret generic backup-s3 \
  --from-literal=ACCESS_KEY_ID=... --from-literal=ACCESS_SECRET_KEY=...
```

```bash
helm upgrade --install secours-tech deploy/helm/secours-tech -n secours-tech \
  --set backup.enabled=true \
  --set backup.destinationPath=s3://<bucket>/secours-tech \
  --set backup.endpointURL=https://<namespace>.compat.objectstorage.<region>.oraclecloud.com \
  --set backup.credentialsSecret=backup-s3
```

## Testing

```bash
./deploy/kind/e2e.sh
```

It creates a kind cluster (or reuses it), builds and loads the image, installs the three
components above, and starts a throwaway S3 endpoint. Then it installs the chart with backup
on and checks four things:
- every migration was applied;
- `secours_tech` is not a superuser;
- both extensions exist;
- an upgrade runs the hook again and applies nothing.

Last, it takes one backup and waits for it to complete. The Helm workflow runs the same script
on every pull request. `kind delete cluster --name secours-tech` removes everything.

Versions are pinned by hand: the component charts in `deploy/kind/e2e.sh` and in this page, the
Postgres image digest in `values.yaml`, and kind and helm with their checksums in
`.github/workflows/helm.yml`.
