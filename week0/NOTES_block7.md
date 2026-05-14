# Week 0 · Block 7 — Kind cluster + raw K8s manifests + minimal Helm chart

**As of:** 2026-05-14 (Wed) · pre-Week 1 · Block 7 complete · **Week 0 done (7/7)**
**Pairs with:** [block7-k8s/](block7-k8s/) — kind config, the FastAPI app, raw
manifests under `k8s/`, and the 5-template chart under `helm/hello-aegis/`
**Reading goal:** so v0.4's real `deploy/helm/aegis` chart (README §10) and the
CI deploy job don't re-discover the kind ingress wiring, the `imagePullPolicy`
trap, or the Helm-comment lexer traps.

---

## 1. What was built

The full local deploy path, twice — by hand, then templated:

```
kind cluster (ingress-ready) → build + `kind load` the app image →
  raw Deployment/Service/Ingress → ingress-nginx → curl localhost  ✓
  then: delete raw → `helm install` the same app from a 5-template chart  ✓
```

**Tooling:** docker 29.1.3, kubectl v1.34.1 (pre-installed); kind 0.31.0 +
helm v4.2.0 installed this block via `winget`. Cluster node image
`kindest/node:v1.35.0`.

**Verified end-to-end:**

| Stage | Evidence |
|---|---|
| kind cluster | node `Ready`, `ingress-ready=true` label set |
| image load | `crictl images` shows `hello-aegis:0.1.0` (55.1 MB) on the node |
| raw deploy | 2 pods `1/1 Running`; `curl localhost` rotates the `pod` field across both replicas → Service load-balances |
| ingress routing | `curl /nonexistent` returns FastAPI's own 404 JSON → traffic reaches the app |
| helm chart | `helm lint` clean, `helm template` server-validates |
| helm install | release `hello-aegis` deployed; `curl` `release` field flips `raw-manifests` → `hello-aegis` |
| helm upgrade | `--set replicaCount=3` → 3 pods, revision 2, `helm history` shows 1 superseded + 2 deployed |

---

## 2. The 5-template Helm chart

`helm/hello-aegis/` — `Chart.yaml`, `values.yaml`, and exactly 5 templates:

| Template | Role |
|---|---|
| `_helpers.tpl` | named-template partials — names, label sets, SA name. Not a manifest; the other 4 `include` it. |
| `deployment.yaml` | the workload — every per-env value is now a `.Values` reference |
| `service.yaml` | stable ClusterIP in front of the pods |
| `ingress.yaml` | host exposure, guarded by `.Values.ingress.enabled` |
| `serviceaccount.yaml` | dedicated workload identity, guarded by `.Values.serviceAccount.create` |

The payoff is visible at runtime: the app echoes `RELEASE_NAME`, which the raw
Deployment hard-codes to `"raw-manifests"` and the chart sets from
`.Release.Name`. Same image, same cluster — `curl` proves which path deployed
the pod.

---

## 3. Gotchas hit live during this block

### 3.1 ingress-nginx **shadows `/healthz`** — your app never sees it

`curl localhost/healthz` returned `200`, `Content-Type: text/html`,
`Content-Length: 0` — not the app's `{"status":"ok"}`. The controller's
generated `nginx.conf` hard-codes, in its server blocks:

```
location /healthz { return 200; }   # for cloud-LB health checks on :80
```

So **any app endpoint at exactly `/healthz` is unreachable through the
Ingress.** This is NOT a bug here: the kubelet's liveness/readiness probes hit
`httpGet /healthz` on the **pod directly**, bypassing the Ingress entirely —
which is why both pods went `1/1 Ready`. The lesson for v0.1+: don't expect to
curl your health endpoint through the ingress; if you ever need it publicly
reachable, name it something else (`/livez`, `/api/health`).

### 3.2 `imagePullPolicy` must not be `Always` for `kind load`-ed images

The image is loaded onto the node with `kind load docker-image`, not pushed to
a registry. `imagePullPolicy: Always` makes the kubelet try to pull
`hello-aegis:0.1.0` from Docker Hub → `ErrImagePull`. Both the raw Deployment
and the chart use `IfNotPresent`, which uses the already-present local image.
v0.1's CI will push to a real registry; this only bites local kind workflows.

### 3.3 Helm parses `{{ }}` **inside YAML comments** — hit twice

`helm` runs every `.yaml` in `templates/` through the Go-template engine
*before* it is YAML. The lexer does not know what a YAML comment is. Two
separate failures from template syntax written inside `#` comments:

- `# ... a {{ .Values.* }} reference` → `bad character U+002A '*'`
- `# The {{- if }} must come first` → `parse error: missing value for if`

**Rule:** never write a literal template action in a comment inside a
`templates/*.yaml` file. Describe it in prose instead.

### 3.4 A leading `{{- if }}` left-chomps the comment block onto `apiVersion:`

`serviceaccount.yaml` / `ingress.yaml` are wrapped in an `if` guard. First
layout put the comment block *above* the guard:

```
# ...comment...
{{- if .Values.x }}      ← {{- eats the comment's trailing newline
apiVersion: v1           ← ...gluing this onto the comment line → commented out
```

`helm template` rendered `# ...comment...apiVersion: v1` — silently invalid
(caught by `kubectl apply --dry-run=server`, not by `helm lint`). **Fix:** the
`{{- if }}` guard must be the *first line* of the file; comments go after it.

### 3.5 A Deployment's `spec.selector` is immutable — raw → Helm is a *replace*

The raw Deployment used `selector: {app: hello-aegis}`; the chart uses
`app.kubernetes.io/{name,instance}` labels. `kubectl apply` of the chart over
the existing Deployment failed: `spec.selector: field is immutable`. You
cannot overlay a chart with different selector labels onto a hand-rolled
Deployment — you must `kubectl delete` the raw objects first, then
`helm install`. This is exactly why the plan says "**re-deployed** via Helm,"
not "patched."

### 3.6 Environment friction (Windows)

- **winget PATH** — `winget install` added kind/helm to PATH persistently but
  the running shell didn't see it; used full binary paths under
  `%LOCALAPPDATA%\Microsoft\WinGet\Packages\...` for the whole block.
- **Git Bash path translation** — `kubectl exec ... -- grep /etc/nginx/...`
  had its container path rewritten to `C:/Program Files/Git/etc/nginx/...`.
  Fix: prefix the command with `MSYS_NO_PATHCONV=1`.
- **kind ingress config** — a default `kind create cluster` cannot route
  Ingress traffic. The cluster config needs `node-labels: ingress-ready=true`
  (so the ingress-nginx "kind" manifest schedules its controller) **and**
  `extraPortMappings` for 80/443 (so host `localhost:80` reaches it).

---

## 4. v0.4 deploy work items that fell out of this block

The real chart is `deploy/helm/aegis` (README §10) and is far larger. This
block validated the *pattern*; v0.4 adds:

1. **Multi-tier + autoscaling** — API and Worker Deployments, HPA (Worker
   scales on Celery queue depth).
2. **StatefulSets** — Postgres / ClickHouse / Redis with PVCs (this block has
   no persistent storage at all).
3. **TLS + NetworkPolicy + PodDisruptionBudget** — none present here.
4. **ServiceAccount + real RBAC** — this block's SA has no Role/RoleBinding;
   v0.4 binds least-privilege rules to it.
5. **Pre-install Job + CronJob** — Alembic + ClickHouse migrations before
   pods start; nightly backups.
6. **Multi-env values** — `values-dev.yaml` / `values-prod.yaml`. This block
   has one `values.yaml`; the `--set replicaCount=3` upgrade proved the
   override mechanism works.
7. **Registry, not `kind load`** — CI builds and pushes; `imagePullPolicy`
   changes accordingly (§3.2).
8. **`helm install` in the CI deploy job** — to the Oracle Cloud K3s cluster
   on every merge to `main`.

---

## 5. What I should be able to defend

1. **"Deployment vs Service vs Ingress?"** → Deployment keeps N pod replicas
   running and rolls them; Service is a stable virtual IP + DNS name that
   load-balances across whatever pods currently match its selector; Ingress is
   the L7 HTTP router that maps host/path to a Service. The Service decouples
   "the address" from "the churning set of pods."

2. **"Why did `curl localhost` need special cluster config?"** → kind runs the
   cluster inside a container. Without `extraPortMappings` 80/443 the host has
   no path in, and without the `ingress-ready` node label the ingress-nginx
   controller won't schedule. Both go in the kind cluster config.

3. **"Why `IfNotPresent` and not `Always`?"** → the image was `kind load`-ed
   onto the node, not pushed to a registry. `Always` would make the kubelet
   try (and fail) to pull it from Docker Hub.

4. **"Why Helm instead of raw manifests?"** → templating (one chart, many
   environments via values), a release lifecycle (`install`/`upgrade`/
   `rollback`/`history`), and atomic `--wait` deploys. The raw manifests
   hard-code every value; the chart parameterised them — `--set
   replicaCount=3` rescaled with one flag.

5. **"`version` vs `appVersion` in Chart.yaml?"** → `version` is the chart's
   own version (bump when templates change); `appVersion` tracks the
   application/image version (bump when the app changes). They move
   independently.

6. **"Why split `selectorLabels` from the full label set in `_helpers.tpl`?"**
   → selector labels go into `Deployment.spec.selector`, which is **immutable**
   (§3.5). They must be a small, stable subset — never include churning labels
   like `app.kubernetes.io/version` or `helm.sh/chart` in the selector.

7. **"How do health probes work if the ingress shadows `/healthz`?"** → probes
   are not HTTP-through-the-ingress. The kubelet does an `httpGet` straight to
   the pod's `containerPort`. The ingress is irrelevant to probe traffic
   (§3.1).

---

## 6. What this block intentionally does NOT do

- **Is not the real `deploy/helm/aegis` chart.** Throwaway under
  `week0/block7-k8s/`. v0.4 builds the real one (§4).
- **Single-node cluster, no persistent storage.** No StatefulSets, no PVCs.
- **No HPA / TLS / NetworkPolicy / PDB / RBAC bindings.** The ServiceAccount
  exists but grants nothing.
- **No registry.** Image distribution is `kind load` only — local-only.
- **No CI/CD.** Deploys were run by hand; v0.4 wires `helm install` into a
  GitHub Actions job.
- **App is trivial on purpose.** `hello-aegis` exists only to be deployed; the
  block is about the deploy path, not the app.

---

## 7. Reproduce / tear down

```bash
# recreate (≈2 min):
kind create cluster --config block7-k8s/kind-config.yaml
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/kind/deploy.yaml
docker build -t hello-aegis:0.1.0 block7-k8s/app
kind load docker-image hello-aegis:0.1.0 --name aegis-block7
helm install hello-aegis ./block7-k8s/helm/hello-aegis --wait

# tear down everything this block created:
kind delete cluster --name aegis-block7
```

The cluster `aegis-block7` is left **running** at end of this block as the
live artifact — delete it with the command above when done poking at it.

---

**Last verified:** 2026-05-14 · raw deploy: 2 pods, ingress routes, Service
load-balances · helm: `install` + `upgrade --set replicaCount=3` (rev 2),
`curl` `release` field confirms Helm-templated · chart + manifests + notes to
be committed to `week0/`.
