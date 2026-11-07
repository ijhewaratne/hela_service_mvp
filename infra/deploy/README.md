# Deployment skeleton (Q67, DEC-08 pending)

**Not connected to any cloud, cluster or registry.** Nothing here has been deployed. It shows how the system is
meant to run once a hosting target is chosen, and it is checked in CI so it does not rot. Every
`*.example.invalid` value is a placeholder. The decision register keeps hosting, region and vendors open.

## What is here

| Path | What it is |
|---|---|
| `services/api/Dockerfile` | One image for the API, the worker and migrations. Runs as UID 10001, no build tools in the final image, defaults to `HELA_ENV=staging` so development keys are refused |
| `apps/admin/Dockerfile`, `apps/admin/nginx.conf` | Static admin console on unprivileged nginx, with a strict CSP whose `connect-src` is set to the API origin at start-up |
| `infra/deploy/kubernetes/base` | Kustomize base: namespace (Pod Security `restricted`), API, worker, migration job, admin, default-deny network policies, ingress |
| `infra/deploy/kubernetes/overlays/staging` | Staging overlay skeleton |
| `infra/monitoring` | Prometheus scrape config and alert rules |

Kubernetes is used as a neutral description, not a decision: the same shape (one image with three commands,
a one-off migration step, private networking, a secret manager, TLS at the edge) maps onto a managed container
service or plain VMs just as well.

## What the platform must provide

* Managed **PostgreSQL 16 with PostGIS** and the `btree_gist` extension; two roles: an owner for migrations and the
  least-privilege runtime role (`infra/docker/postgres-init` shows the grants). Continuous WAL archiving / PITR for
  the RPO ≤ 15 min goal (NFR-06); the local backup drill alone cannot meet it.
* **Redis** (rate limiting fails closed if it is down).
* **S3-compatible object storage** for evidence. Objects are already encrypted by the application; keep the bucket
  private, with versioning and no public access.
* **ClamAV** reachable on TCP 3310 (uploads fail closed without it).
* A **secret manager** that creates the `hela-secrets` and `hela-migration` Secrets (names and keys listed in
  `base/config.yaml`), and in time a **KMS** for field and signing keys (`docs/security/KEY_ROTATION_RUNBOOK.md`).
* **TLS certificates** for the API and admin hosts, and an **ingress controller**. The admin host must be limited to
  staff networks (Q73), using the controller's allowlist mechanism.
* **Prometheus and Alertmanager** (or an equivalent that can evaluate `infra/monitoring/prometheus/alerts.yml`) and
  central log collection (§17). Logs contain no personal data, but treat them as confidential anyway.
* An **SMS vendor** (DEC-08). Until one is integrated, staging has no working sign-in by SMS.

## Release order

1. Take a backup. Migration 0005 is irreversible.
2. Run the `hela-migrate` job with the owner credentials. It must finish before the new API rolls out.
3. Roll out `hela-api` (two replicas, no unavailability during the rollout) and `hela-worker`.
4. Roll out `hela-admin`.
5. Check `/health`, the `HelaWorkerHeartbeatMissing` alert, and `python -m hela.cli verify-audit`.

## Checks run locally and in CI

```bash
kubectl kustomize infra/deploy/kubernetes/overlays/staging > rendered.yaml
docker run --rm -v "$PWD":/w:ro ghcr.io/yannh/kubeconform:v0.6.7 -strict -summary /w/rendered.yaml
docker build -t hela-api:dev services/api
docker build --build-arg VITE_API_BASE=https://api.example.invalid -t hela-admin:dev apps/admin
```

## Not done (needs the hosting decision)

* Infrastructure-as-code for the managed services (Terraform/OpenTofu or the provider's equivalent).
* Image signing, digest pinning and an admission policy that only allows signed images.
* Horizontal autoscaling, which needs pilot load figures (NFR-11).
* Real staging and production overlays: hostnames, CIDRs, controller-specific allowlist, resource sizing.
