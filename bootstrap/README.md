# Bootstrap: local cluster + Argo CD

This folder contains the one-time, imperative steps to create a local Kubernetes cluster and install only Argo CD. Everything else (Airflow, DAG sync, and supporting services) is reconciled by Argo CD Applications declared in the `platform/` folder.

## 1) Create a local cluster (Kind example)
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

> If you prefer k3d, create an equivalent cluster with two worker nodes and expose 8080/8443 for web UI access.
> The Kind control-plane node is tainted (`node-role.kubernetes.io/control-plane:NoSchedule`), so tolerations are baked into the values files. Multi-node clusters can optionally add workers and steer workloads with `nodeSelector`/`affinity`.

## 2) Pre-create namespaces
```bash
kubectl create namespace argocd
kubectl create namespace airflow-cron
kubectl create namespace airflow-user
```

## 3) Install Argo CD (only imperative apply)
Apply the upstream Argo CD install manifest, then patch the server service type for convenience:
```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "LoadBalancer"}}'
```

Port-forward if your local distro does not support LoadBalancer services:
```bash
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

## 4) Bootstrap Argo CD with the platform repo
From now on, avoid `kubectl apply` for Airflow; instead, point Argo CD at the `platform/argo-apps` directory. Either log into the Argo CD UI or CLI:
```bash
# First-time password is the argocd-server pod name. Change it once logged in.
argocd login localhost:8080 --username admin --password <ARGOCD_POD_NAME> --insecure
argocd repo add <YOUR_PLATFORM_REPO_URL>
argocd app create app-of-apps \
  --repo <YOUR_PLATFORM_REPO_URL> \
  --path platform/argo-apps \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace argocd \
  --sync-policy automated
```

Argo CD will now own the lifecycle of the Airflow releases and DAG sync configuration.

> Tip: once the app-of-apps is created, run `argocd app wait app-of-apps --health --timeout 300` to block until the two Airflow Applications are Healthy before you start the demo.
