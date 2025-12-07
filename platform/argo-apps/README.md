# Argo CD Applications

Point Argo CD at this folder (`platform/argo-apps`) with automated sync. The manifests here create two Applications—one per namespace—that pull the official Airflow chart through a tiny Helm wrapper stored in `platform/airflow/*`. Set `repoURL` to this repository and adjust `targetRevision` if your branch is not `main`.

- `airflow-cron-app.yaml`: installs Airflow into `airflow-cron` with values from `platform/airflow/cron/values.yaml`.
- `airflow-user-app.yaml`: installs Airflow into `airflow-user` with values from `platform/airflow/user/values.yaml`.

Set `repoURL` in each manifest to the Git URL hosting this repository before syncing.
