import datetime as dt
from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="example_user_dag",
    schedule_interval=None,
    start_date=dt.datetime(2024, 1, 1),
    catchup=False,
    tags=["user", "kubernetes-executor"],
) as dag:
    BashOperator(
        task_id="echo-user",
        bash_command="echo 'User-triggered DAG running in airflow-user namespace' && sleep 5",
    )
