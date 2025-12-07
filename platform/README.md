# Platform manifests driven by Argo CD

This folder is synced by Argo CD. It contains the Application definitions and the Helm values for Airflow releases in both namespaces.

## Contents
- `argo-apps/`: App-of-Apps definition for Argo CD plus per-namespace Airflow Applications.
- `airflow/cron/values.yaml`: Helm values for Airflow 2.6.3 using the KubernetesExecutor in `airflow-cron`.
- `airflow/user/values.yaml`: Helm values for the user-triggered namespace.

Argo CD should point to `platform/argo-apps` (see `bootstrap/README.md`). Once synced, the two Airflow releases will be reconciled without manual `kubectl` or `helm` commands.

Both values files include tolerations for the Kind control-plane taint and placeholders for `nodeSelector`/`affinity` so you can steer cron vs. user workloads to distinct pools in larger clusters. They also ship with `dags.gitSync` pointing to an **external DAG repository**—configure the repo URL plus optional SSH/basic-auth secrets if the repo is private so Argo CD can pull DAGs continuously.
