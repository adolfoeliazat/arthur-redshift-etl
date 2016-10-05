#!/usr/bin/env bash

if [[ $# -lt 1 || $# -gt 2 || "$1" = "-h" ]]; then
    echo "Usage: `basename $0` <bucket_name> [<environment>]"
    echo "The environment defaults to 'development'."
    exit 0
fi

CLUSTER_BUCKET="${1?'Missing bucket name'}"
CLUSTER_ENVIRONMENT="${2-development}"

# Verify that this bucket/environment pair is set up on s3
BOOTSTRAP="s3://$CLUSTER_BUCKET/$CLUSTER_ENVIRONMENT/bootstrap/bootstrap.sh"
if ! aws s3 ls "$BOOTSTRAP" > /dev/null; then
    echo "Invalid parameters!"
    exit 1
fi

PIPELINE_ID_FILE="/tmp/pipeline_id_${USER}_$$.json"

set -e -x

aws datapipeline create-pipeline \
    --name "ETL Loader Pipeline ($CLUSTER_ENVIRONMENT)" \
    --unique-id redshift_load_pipeline \
    | tee "$PIPELINE_ID_FILE"

PIPELINE_ID=`jq --raw-output < "$PIPELINE_ID_FILE" '.pipelineId'`

aws datapipeline put-pipeline-definition \
    --pipeline-definition file://./aws_config/ec2_data_pipeline.json \
    --parameter-values myS3Bucket="$CLUSTER_BUCKET" myEtlEnvironment="$CLUSTER_ENVIRONMENT" \
    --pipeline-id "$PIPELINE_ID"

aws datapipeline activate-pipeline --pipeline-id "$PIPELINE_ID"
