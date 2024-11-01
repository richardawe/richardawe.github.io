# Import required libraries
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python_operator import PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.providers.snowflake.operators.snowflake import SnowflakeOperator
from azure.storage.blob import BlobServiceClient
from pyspark.sql import SparkSession
import pandas as pd
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Azure Storage configuration
AZURE_STORAGE_CONNECTION_STRING = "YOUR_AZURE_STORAGE_CONNECTION_STRING"
AZURE_CONTAINER = "raw-data"

# Snowflake configuration
SNOWFLAKE_CONN_ID = "snowflake_default"
WAREHOUSE = "ANALYTICS_WH"
DATABASE = "ENTERPRISE_DB"
SCHEMA = "PUBLIC"

# Define default DAG arguments
default_args = {
    'owner': 'data_engineering',
    'depends_on_past': False,
    'start_date': datetime(2024, 1, 1),
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 3,
    'retry_delay': timedelta(minutes=5)
}

def extract_from_source():
    """Extract data from various source systems and load to Azure Blob Storage"""
    try:
        # Initialize Azure Blob Storage client
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(AZURE_CONTAINER)
        
        # Example: Extract data from a source system (replace with your actual source)
        source_data = pd.read_csv('source_system.csv')
        
        # Upload to Azure Blob Storage
        blob_name = f"raw_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        container_client.upload_blob(
            name=blob_name,
            data=source_data.to_csv(index=False),
            overwrite=True
        )
        logger.info(f"Successfully uploaded {blob_name} to Azure Blob Storage")
        return blob_name
        
    except Exception as e:
        logger.error(f"Error in extract_from_source: {str(e)}")
        raise

def transform_with_spark():
    """Transform data using Spark"""
    spark = SparkSession.builder \
        .appName("DataTransformation") \
        .config("spark.jars.packages", "com.microsoft.azure:azure-storage-blob-spark:12.0.0") \
        .getOrCreate()
    
    try:
        # Read data from Azure Blob Storage
        df = spark.read.format("csv") \
            .option("header", "true") \
            .load(f"wasbs://{AZURE_CONTAINER}@YOUR_STORAGE_ACCOUNT.blob.core.windows.net/*.csv")
        
        # Perform transformations
        transformed_df = df \
            .withColumn("processed_date", spark.functions.current_date()) \
            .withColumn("year", spark.functions.year("date_column")) \
            .withColumn("month", spark.functions.month("date_column"))
        
        # Write transformed data back to Azure
        transformed_df.write \
            .mode("overwrite") \
            .parquet(f"wasbs://processed-data@YOUR_STORAGE_ACCOUNT.blob.core.windows.net/")
            
        logger.info("Successfully completed Spark transformations")
        
    except Exception as e:
        logger.error(f"Error in transform_with_spark: {str(e)}")
        raise
    finally:
        spark.stop()

# Create Snowflake tables
create_tables_sql = """
CREATE TABLE IF NOT EXISTS sales_fact (
    sale_id INTEGER,
    product_id INTEGER,
    customer_id INTEGER,
    sale_date DATE,
    amount DECIMAL(10,2),
    processed_date DATE
);

CREATE TABLE IF NOT EXISTS customer_dimension (
    customer_id INTEGER,
    customer_name VARCHAR(100),
    segment VARCHAR(50),
    region VARCHAR(50)
);
"""

# Load data to Snowflake
load_to_snowflake_sql = """
COPY INTO sales_fact
FROM @azure_stage/processed-data/
FILE_FORMAT = (TYPE = PARQUET)
ON_ERROR = CONTINUE;
"""

# Define the DAG
with DAG(
    'enterprise_data_pipeline',
    default_args=default_args,
    description='Enterprise data pipeline for analytics',
    schedule_interval='0 */4 * * *',  # Runs every 4 hours
    catchup=False
) as dag:

    # Task 1: Extract data from source systems
    extract_task = PythonOperator(
        task_id='extract_from_source',
        python_callable=extract_from_source
    )

    # Task 2: Transform data with Spark
    transform_task = SparkSubmitOperator(
        task_id='transform_with_spark',
        application='transform_with_spark.py',
        conn_id='spark_default',
        conf={
            'spark.driver.memory': '4g',
            'spark.executor.memory': '4g'
        }
    )

    # Task 3: Create Snowflake tables
    create_tables = SnowflakeOperator(
        task_id='create_snowflake_tables',
        sql=create_tables_sql,
        snowflake_conn_id=SNOWFLAKE_CONN_ID,
        warehouse=WAREHOUSE,
        database=DATABASE,
        schema=SCHEMA
    )

    # Task 4: Load data to Snowflake
    load_to_snowflake = SnowflakeOperator(
        task_id='load_to_snowflake',
        sql=load_to_snowflake_sql,
        snowflake_conn_id=SNOWFLAKE_CONN_ID,
        warehouse=WAREHOUSE,
        database=DATABASE,
        schema=SCHEMA
    )

    # Define task dependencies
    extract_task >> transform_task >> create_tables >> load_to_snowflake
```