"""
Weekly APRA superannuation pipeline DAG.
Schedule: every Monday at 8am AEST.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.providers.slack.operators.slack_webhook import SlackWebhookOperator

default_args = {
    "owner": "data-engineering",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": None,  # replaced by Slack alert task
}

with DAG(
    dag_id="apra_super_pipeline",
    default_args=default_args,
    start_date=datetime(2025, 1, 6),
    schedule_interval="0 22 * * 0",  # Sunday 10pm UTC = Monday 8am AEST
    catchup=False,
    tags=["apra", "superannuation", "pipeline"],
) as dag:

    download = BashOperator(
        task_id="download_apra_files",
        bash_command="cd /opt/airflow && python -m ingestion.download_apra",
    )

    upload_s3 = BashOperator(
        task_id="upload_to_s3",
        bash_command="cd /opt/airflow && python -m ingestion.upload_to_s3",
    )

    load_postgres = BashOperator(
        task_id="load_to_postgres",
        bash_command="cd /opt/airflow && python -m ingestion.load_to_postgres",
    )

    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command="cd /opt/airflow/dbt_project && dbt run --target prod",
    )

    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command="cd /opt/airflow/dbt_project && dbt test --target prod",
    )

    slack_success = SlackWebhookOperator(
        task_id="slack_notify_success",
        slack_webhook_conn_id="slack_default",
        message=":white_check_mark: APRA pipeline completed successfully — {{ ds }}",
        trigger_rule="all_success",
    )

    slack_failure = SlackWebhookOperator(
        task_id="slack_notify_failure",
        slack_webhook_conn_id="slack_default",
        message=":x: APRA pipeline FAILED on {{ ds }} — check Airflow logs",
        trigger_rule="one_failed",
    )

    download >> upload_s3 >> load_postgres >> dbt_run >> dbt_test
    dbt_test >> [slack_success, slack_failure]
