"""
PySpark alternative to the dbt aggregations — reads raw APRA parquet from S3.
Run locally:  spark-submit spark/quarterly_aggregation.py
Run on EMR:   aws emr-serverless start-job-run ... (see README)
"""
import os
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

S3_BUCKET = os.environ.get("S3_BUCKET", "super-pipeline")

spark = (
    SparkSession.builder.appName("APRA Super Quarterly Aggregation")
    .config("spark.sql.adaptive.enabled", "true")
    .getOrCreate()
)

df = spark.read.parquet(f"s3a://{S3_BUCKET}/processed/apra_mysuper/")

quarter_window = Window.partitionBy("quarter_date")
fund_window = Window.partitionBy("abn").orderBy("quarter_date")

fund_perf = (
    df.filter(F.col("return_5yr").isNotNull())
    .withColumn(
        "rank_5yr_overall",
        F.rank().over(quarter_window.orderBy(F.col("return_5yr").desc())),
    )
    .withColumn(
        "fee_percentile",
        F.percent_rank().over(quarter_window.orderBy("total_fee_pct")),
    )
    .withColumn("prev_members", F.lag("member_accounts").over(fund_window))
    .withColumn("member_net_change", F.col("member_accounts") - F.col("prev_members"))
)

(
    fund_perf.write.mode("overwrite")
    .parquet(f"s3a://{S3_BUCKET}/aggregated/fund_performance/")
)

print("Done. Rows written:", fund_perf.count())
spark.stop()
