# Airflow 2.6.3 on local Kubernetes with Argo CD (multi-namespace worker PoC)

This PoC stands up **one Airflow 2.6.3 deployment** (webserver, scheduler, triggerer, MySQL) in a single namespace while using the **KubernetesExecutor** in **multi-namespace mode** so task pods run in two other namespaces (`airflow-cron` and `airflow-user`). Everything is installed by **Argo CD**—no manual `helm install` or `kubectl apply` after bootstrap.

## Quick flow (what you actually do)
1. **Create the Kind cluster** with the provided config so taints are already covered.
2. **Create namespaces**: `argocd` (GitOps), `airflow-core` (webserver/scheduler/triggerer/DB), and the two worker namespaces `airflow-cron` and `airflow-user`.
3. **Install Argo CD** once and log in.
4. **Prepare the external DAG repo** (or point to your own) and create the PAT-backed secret in the **airflow-core** namespace if private.
5. **Update the single values file** (`platform/airflow/core/values.yaml`) to reference the external DAG repo and the worker namespace list.
6. **Register this platform repo in Argo CD** and create the Application (`platform/argo-apps/airflow-core-app.yaml`).
7. **Wait for Argo CD to become Healthy/Synced** and validate pods, counts, and worker placement with the commands below.

## Repo layout
- `bootstrap/`: one-time, imperative steps to create the local cluster and install Argo CD.
- `platform/`: Argo CD Application and Helm values that deploy a single Airflow release (core namespace) with multi-namespace workers.

> DAGs live in a **separate Git repository**. This repo intentionally contains no DAG files to avoid confusion—see “Prepare the external DAG repo” below.

## Prerequisites
- `kubectl`, `kind` (or another local Kubernetes distro), and `argocd` CLI.
- Internet access to pull images and chart dependencies.
- GitHub Personal Access Token (classic) if your DAG repository will be private.

## 1) Create the local cluster (Kind example)
```bash
cat > kind-airflow.yaml <<'YAML'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    image: kindest/node:v1.29.4
    extraPortMappings:
      - containerPort: 30080
        hostPort: 8080
      - containerPort: 30443
        hostPort: 8443
    kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            eviction-hard: nodefs.available<5%,nodefs.inodesFree<5%
            image-gc-high-threshold: "90"
            image-gc-low-threshold: "80"
YAML
kind create cluster --name airflow --config kind-airflow.yaml
```

Verify the cluster is reachable and ready:
```bash
kubectl cluster-info --context kind-airflow
kubectl get nodes -o wide
kubectl get pods -A
```
You should see one control-plane node `Ready` with only system pods running.

> Kind control-plane nodes are tainted by default (`node-role.kubernetes.io/control-plane:NoSchedule`). The provided values file already includes tolerations so everything schedules even on a single node. On multi-node clusters you can add `nodeSelector`/`affinity` later.

## 2) Pre-create namespaces
```bash
kubectl create namespace argocd
kubectl create namespace airflow-core
kubectl create namespace airflow-cron
kubectl create namespace airflow-user
```

Verify:
```bash
kubectl get ns argocd airflow-core airflow-cron airflow-user
```
All four namespaces should appear with `Active` status.

## 3) Install Argo CD (only imperative apply)
```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "LoadBalancer"}}'
# Port-forward if your distro lacks LoadBalancer support
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

Verify Argo CD components and admin login:
```bash
kubectl get pods -n argocd -w  # wait until all pods are Ready
kubectl get svc argocd-server -n argocd
kubectl get events -n argocd --sort-by=.lastTimestamp | tail -n 5
```
Log in once (default password is the `argocd-server` pod name) and change it:
```bash
argocd login localhost:8080 --username admin --password <ARGOCD_POD_NAME> --insecure
argocd account update-password
```

## 4) Prepare the external DAG repo (GitHub example)
Create a new Git repo (e.g., `https://github.com/your-org/airflow-dags.git`) with this layout:
```
dags/
  example_cron_dag.py
  example_user_dag.py
```

Sample DAG content you can drop into that repo. Note how each DAG pins its **worker namespace** via `executor_config` to take advantage of multi-namespace mode:
```python
# dags/example_cron_dag.py
from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime, timedelta

with DAG(
    dag_id="example_cron_dag",
    start_date=datetime(2023, 1, 1),
    schedule="*/5 * * * *",
    catchup=False,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=1)},
    tags=["cron"],
) as dag:
    BashOperator(
        task_id="say_hi",
        bash_command="echo 'hello from cron namespace'",
        executor_config={"KubernetesExecutor": {"namespace": "airflow-cron"}},
    )
```
```python
# dags/example_user_dag.py
from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime

with DAG(
    dag_id="example_user_dag",
    start_date=datetime(2023, 1, 1),
    schedule=None,
    catchup=False,
    tags=["user"],
) as dag:
    BashOperator(
        task_id="ad_hoc",
        bash_command="echo 'hello from user namespace'",
        executor_config={"KubernetesExecutor": {"namespace": "airflow-user"}},
    )
```

### If the DAG repo is private (GitHub PAT classic)
1. Create a GitHub Classic PAT with `repo` scope.
2. Store it as a Kubernetes secret in **airflow-core** only (the git-sync sidecars run there):
   ```bash
   kubectl create secret generic dags-git-basic -n airflow-core \
     --from-literal=GIT_SYNC_USERNAME=<github-username> \
     --from-literal=GIT_SYNC_PASSWORD=<github-classic-pat>
   ```
3. In the values file below, set `dags.gitSync.credentialsSecret: dags-git-basic` and keep `sshKeySecret` empty (or vice versa for SSH keys).

Verify the secret exists:
```bash
kubectl get secret dags-git-basic -n airflow-core
```

## 5) Point the values file at your DAG repo and namespaces
Edit `platform/airflow/core/values.yaml`:
- `dags.gitSync.repo`: set to your DAG repo URL (HTTPS works best with the PAT secret above).
- `dags.gitSync.branch`: branch containing your DAGs (default `main`).
- `dags.gitSync.credentialsSecret` or `dags.gitSync.sshKeySecret`: set based on your auth choice.
- `config.AIRFLOW__KUBERNETES__NAMESPACE_LIST`: keep `airflow-core,airflow-cron,airflow-user` (or expand if you add more worker namespaces).

Quick consistency check:
```bash
grep -E "repo:|branch:|credentialsSecret|sshKeySecret|NAMESPACE_LIST" platform/airflow/core/values.yaml
```
Ensure the value file points to the external DAG repository and lists all namespaces where workers may run.

## 6) Let Argo CD deploy everything (GitOps only)
Add this repo to Argo CD and create the Application pointing at `platform/argo-apps/airflow-core-app.yaml`:
```bash
argocd repo add <YOUR_PLATFORM_REPO_URL>
argocd app create airflow-core \
  --repo <YOUR_PLATFORM_REPO_URL> \
  --path platform/airflow/core \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace airflow-core \
  --sync-policy automated
argocd app wait airflow-core --health --timeout 300
```

Verify Application health:
```bash
argocd app get airflow-core
argocd app list --output wide
```
`airflow-core` should show `Healthy` and `Synced`. Argo CD renders the chart and installs the Airflow Helm release using `platform/airflow/core/values.yaml`.

## 7) Understand what runs where (official model)
- **Single Airflow instance:** one webserver, one scheduler, one triggerer Deployment, and one metadata database (`airflow-mysql-0` StatefulSet) all run in the **airflow-core** namespace. This follows the official Helm chart pattern.
- **Workers fan out:** with `multi_namespace_mode` enabled and `NAMESPACE_LIST` set, each task’s worker pod runs in the namespace requested by the DAG’s `executor_config` (e.g., `airflow-cron` or `airflow-user`).
- **Shared DAGs/config:** the core pods mount the git-synced DAG volume so the scheduler can launch tasks across namespaces while keeping a single source of truth.
- **Security/Isolation:** worker pods in `airflow-cron` and `airflow-user` only need the permissions/secrets you grant there; the core secrets stay in `airflow-core`.

### Component placement cheat sheet
| Component                          | Namespace       | Steady-state pods | Containers per pod | How to verify |
|------------------------------------|-----------------|-------------------|--------------------|---------------|
| webserver                          | `airflow-core`  | 1                 | 2 (webserver + git-sync) | `kubectl get pod -n airflow-core -l component=webserver`
| scheduler                          | `airflow-core`  | 1                 | 2 (scheduler + git-sync) | `kubectl get pod -n airflow-core -l component=scheduler`
| triggerer                          | `airflow-core`  | 1                 | 2 (triggerer + git-sync) | `kubectl get pod -n airflow-core -l component=triggerer`
| metadata DB (MySQL)                | `airflow-core`  | 1 StatefulSet pod | 1                  | `kubectl get sts -n airflow-core airflow-mysql`
| worker pods (per task, transient)  | `airflow-cron` or `airflow-user` | 0 idle (spawn on demand) | 1 | `kubectl get pod -n airflow-cron` / `kubectl get pod -n airflow-user`

Use the table during demos to confirm you are looking at the correct namespace and that pod/container counts align with expectations before and after triggering DAGs.

### Command-friendly diagram: who runs where
```
             [ External DAG Git Repo ]
                      |
                      v
  +----------------- airflow-core namespace -----------------+
  |                                                          |
  |  git-sync  git-sync  git-sync                            |
  |    |          |        |                                |
  |    v          v        v                                |
  |  webserver  scheduler triggerer   MySQL (metadata)      |
  |     |          |        |               |               |
  +---------------------------------------------------------+
        |          |        |               |
        |          |        |               +--> runs migrations Job once
        |          |        | 
        |          |        +--> launches worker pods via Kubernetes API
        |          | 
        |          +------------ schedules DAGs using git-synced files
        |
        +----------------------- serves UI & REST API

  Worker pods (per task) land in their target namespaces:
  airflow-cron:  airflow-worker-<run-id>-<task>
  airflow-user:  airflow-worker-<run-id>-<task>
```

### Baseline footprint in Kubernetes (before triggering DAGs)
- **Nodes:** 1 Kind control-plane node (unless you create more). Tainted by default; tolerations already set in the values file.
- **Argo CD namespace (`argocd`):** 7–8 pods (api-server, repo-server, application-controller, dex, redis, server, notifications) depending on version. 7+ containers.
- **Airflow core namespace (`airflow-core`):** **4 pods / 7 containers steady-state** after migrations:
  - `airflow-webserver` (1 pod, 2 containers: webserver + git-sync)
  - `airflow-scheduler` (1 pod, 2 containers: scheduler + git-sync)
  - `airflow-triggerer` (1 pod, 2 containers: triggerer + git-sync)
  - `airflow-mysql-0` (1 pod, 1 container)
- **Worker namespaces (`airflow-cron`, `airflow-user`):** **0 pods idle** until you trigger DAGs. Each task will create a transient `airflow-worker-...` pod in the namespace specified by `executor_config`.

## 8) How pods spin up from Argo CD sync to DAG completion
1. **Argo CD syncs the Application** → renders the Airflow Helm chart and creates Deployments/StatefulSets/Jobs (no manual Helm/Kubectl).
2. **Database first:** Kubernetes starts `airflow-mysql-0` (1 container) in `airflow-core` so migrations have a target.
3. **Migrations next:** the chart’s `run-airflow-migrations` Job spins up once in `airflow-core`, runs Airflow DB migrations against MySQL, and then exits.
4. **Core services start:** Deployments come up in `airflow-core`:
   - `airflow-webserver` (2 containers: webserver + git-sync sidecar)
   - `airflow-scheduler` (2 containers: scheduler + git-sync sidecar)
   - `airflow-triggerer` (2 containers: triggerer + git-sync sidecar)
   Each pod pulls DAGs from the external repo via git-sync before marking Ready.
5. **Steady state reached:** you now have 4 pods / 7 containers in `airflow-core`. Git-sync continues to poll the DAG repo on its interval.
6. **DAG triggered:** when you trigger `example_cron_dag`, the scheduler launches a transient `airflow-worker-...` pod **in `airflow-cron`**. Triggering `example_user_dag` does the same in **`airflow-user`**.
7. **Task executes and pod exits:** the worker pod runs the task, streams logs to the webserver UI, and terminates on success/failure. No workers remain once tasks finish.
8. **Repeat:** subsequent DAG runs repeat steps 6–7; core services stay up, git-sync keeps DAGs fresh, and Argo CD keeps manifests drift-free.

### Command-friendly lifecycle diagram
```
argocd app sync airflow-core
         |
         v
  [Argo CD applies manifests]
         |
         v
  (1) StatefulSet: airflow-mysql-0  -----------+
         |                                      |
         v                                      |
  (2) Job: run-airflow-migrations               |
         |                                      |
         v                                      |
  (3) Deployments in airflow-core               |
      - airflow-webserver (w/ git-sync)         |
      - airflow-scheduler (w/ git-sync)         |
      - airflow-triggerer (w/ git-sync)         |
                                                |
  Steady state: 4 pods / 7 containers           |
         |                                      |
         v                                      |
  airflow dags trigger <dag_id>                 |
         |                                      |
         v                                      |
  Scheduler asks Kubernetes API to start worker |
         |                                      |
         v                                      |
  airflow-worker-... pod in airflow-cron|user   |
         |                                      |
         v                                      |
  Task finishes -> worker pod completes --------+
```

## 9) Confirm pod placements and counts
After the `run-airflow-migrations` Job finishes, you should see in `airflow-core`:
- `airflow-scheduler-...` (1 pod, **2 containers**: scheduler + git-sync)
- `airflow-webserver-...` (1 pod, **2 containers**: webserver + git-sync)
- `airflow-triggerer-...` (1 pod, **2 containers**: triggerer + git-sync)
- `airflow-mysql-0` (1 pod, **1 container**)

Check readiness and counts:
```bash
kubectl get pods -n airflow-core -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[*].ready,CONTAINERS:.spec.containers[*].name
kubectl get jobs -n airflow-core
kubectl get pods -n airflow-cron
kubectl get pods -n airflow-user
```
Total steady-state: **4 pods / 7 containers in airflow-core**, **0 worker pods** elsewhere until tasks run.

## 10) Verify Airflow version, executor, and DAG routing
```bash
# Version + executor (core namespace)
kubectl exec -n airflow-core deploy/airflow-webserver -- airflow version
kubectl exec -n airflow-core deploy/airflow-webserver -- airflow config get-value core executor
kubectl exec -n airflow-core deploy/airflow-webserver -- airflow config get-value kubernetes namespace_list
kubectl exec -n airflow-core deploy/airflow-webserver -- airflow config get-value kubernetes multi_namespace_mode

# DAG visibility (UI): both DAGs appear in the same Airflow UI
```

Trigger DAGs and watch pods/logs:
```bash
# Cron DAG -> airflow-cron worker
kubectl exec -n airflow-core deploy/airflow-webserver -- airflow dags trigger example_cron_dag
kubectl get pods -n airflow-cron -w
kubectl logs -n airflow-core deploy/airflow-scheduler --tail 50

# User DAG -> airflow-user worker
kubectl exec -n airflow-core deploy/airflow-webserver -- airflow dags trigger example_user_dag
kubectl get pods -n airflow-user -w
kubectl logs -n airflow-core deploy/airflow-scheduler --tail 50
```
Validation checklist:
- Each trigger creates one short-lived `airflow-worker-...` pod in the requested namespace and it completes successfully.
- Scheduler logs show the task is picked up and finished without retries.
- `kubectl logs <worker-pod> -c base -n <namespace>` shows the Bash output defined in the DAG.

## 11) Monitor health after deployment
```bash
# Pod/status overview
kubectl get pods -n airflow-core -o wide
kubectl get pods -n airflow-cron -o wide
kubectl get pods -n airflow-user -o wide

# Argo CD health/sync
argocd app list
argocd app get airflow-core

# If metrics-server is installed
kubectl top pod -n airflow-core
kubectl top pod -n airflow-cron
kubectl top pod -n airflow-user
```
Use `argocd app get` to confirm the live manifest matches Git (`Synced`) and that health is `Healthy`. CPU/memory stats validate resource usage during demos.

## 12) Scheduling controls (taints, selectors, affinity)
- Kind’s control-plane taint is tolerated by default in the values file.
- To steer cron vs. user workloads onto different pools, set `nodeSelector`, `tolerations`, or `affinity` blocks on the **worker namespaces’** service accounts/roles if you add more nodes. In this single-node PoC the defaults already tolerate the taint.
- To prove placement during a demo:
  ```bash
  kubectl describe pod -n airflow-core <pod-name> | grep -E "^Node:|Tolerations|Node-Selectors" -A3
  kubectl describe pod -n airflow-cron <pod-name> | grep -E "^Node:|Tolerations|Node-Selectors" -A3
  kubectl describe pod -n airflow-user <pod-name> | grep -E "^Node:|Tolerations|Node-Selectors" -A3
  ```

## 13) Moving toward production later
- Swap the embedded MySQL for your managed database (or external Postgres/MySQL) and enable persistence.
- Add auth (OIDC/SAML) via `webserver` env/config.
- Wire an external secrets manager instead of inline values.
- Tune resources and Airflow concurrency.
