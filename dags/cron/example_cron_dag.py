import datetime as dt
from airflow import DAG
from airflow.operators.python import PythonOperator


def _cron_task(**context):
    print("Running cron DAG in airflow-cron namespace; task_id=", context["task"].task_id)


def _emit_metrics():
    import random
    count = random.randint(1, 5)
    print(f"Generated {count} synthetic cron events")


default_args = {
    "owner": "cron-team",
    "retries": 1,
    "retry_delay": dt.timedelta(minutes=1),
}

with DAG(
    dag_id="example_cron_dag",
    default_args=default_args,
    schedule_interval="*/5 * * * *",
    start_date=dt.datetime(2024, 1, 1),
    catchup=False,
    tags=["cron", "kubernetes-executor"],
) as dag:
    PythonOperator(task_id="cron-task", python_callable=_cron_task)
    PythonOperator(task_id="emit-metrics", python_callable=_emit_metrics)
