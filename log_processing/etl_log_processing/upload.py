"""
Uploader and Lambda handler -- send log records to Elasticsearch.

From the command line, pick local files or files in S3, parse their content, and send
log records to ES domain.

As a lambda handler, extract the new file in S3 from the event information,
parse that file and send it to ES domain.
"""

import io
import gzip
import urllib.parse
import sys

import boto3
import elasticsearch
import elasticsearch.helpers
import requests_aws4auth

from etl_log_processing import config
from etl_log_processing import parser


def load_records(sources):
    for source in sources:
        if source == "examples":
            for record in parser.create_example_records():
                yield record
        else:
            if source.startswith("s3://"):
                for full_name in list_files(source):
                    for record in _load_records_using(load_remote_content, full_name):
                        yield record
            else:
                for record in _load_records_using(load_local_content, source):
                    yield record


def _load_records_using(content_opener, content_location):
    lines = content_opener(content_location)
    log_parser = parser.LogParser(content_location)
    return log_parser.split_log_lines(lines)


def load_local_content(filename):
    print("Parsing '{}'".format(filename), file=sys.stderr)
    if filename.endswith(".gz"):
        with gzip.open(filename, 'rb') as f:
            lines = f.read().decode()
    else:
        with open(filename, 'r') as f:
            lines = f.read()
    return lines


def _split_uri_for_s3(uri):
    split_result = urllib.parse.urlsplit(uri)
    if split_result.scheme != "s3":
        raise ValueError("scheme {} not supported".format(split_result.scheme))
    return split_result.netloc, split_result.path.lstrip('/')


def load_remote_content(uri):
    print("Parsing '{}'".format(uri), file=sys.stderr)
    bucket_name, object_key = _split_uri_for_s3(uri)
    s3 = boto3.resource("s3")
    bucket = s3.Bucket(bucket_name)
    obj = bucket.Object(object_key)
    response = obj.get()['Body']
    if object_key.endswith(".gz"):
        stream = io.BytesIO(response.read())
        lines = gzip.GzipFile(fileobj=stream).read().decode()
    else:
        lines = response.read().decode()
    return lines


def list_files(uri):
    bucket_name, prefix = _split_uri_for_s3(uri)
    s3 = boto3.resource("s3")
    bucket = s3.Bucket(bucket_name)
    for summary in bucket.objects.filter(Prefix=prefix):
        yield "s3://{}/{}".format(summary.bucket_name, summary.key)


def aws_auth():
    # https://github.com/sam-washington/requests-aws4auth/pull/2
    print("Retrieving credentials", file=sys.stderr)
    session = boto3.Session()
    credentials = session.get_credentials()
    aws4auth = requests_aws4auth.AWS4Auth(credentials.access_key, credentials.secret_key, config.MyConfig.region, "es",
                                          session_token=credentials.token)

    def wrapped_aws4auth(request):
        return aws4auth(request)

    return wrapped_aws4auth


def _connect_to_es(http_auth):
    print("Connecting to ES endpoint: {}".format(config.MyConfig.endpoint), file=sys.stderr)
    es = elasticsearch.Elasticsearch(
        hosts=[{"host": config.MyConfig.endpoint, "port": 443}],
        use_ssl=True,
        verify_certs=True,
        connection_class=elasticsearch.connection.RequestsHttpConnection,
        http_auth=http_auth,
        send_get_body_as="POST"
    )
    return es


def _create_index(client):
    print("Creating index {} ({})".format(config.MyConfig.index, config.MyConfig.doc_type), file=sys.stderr)
    client.indices.create(index=config.MyConfig.index, body=parser.LogParser.index(), ignore=400)
    body = dict(mappings={})
    body["mappings"][config.MyConfig.doc_type] = parser.LogParser.index()
    client.indices.create(index=config.MyConfig.index, body=body, ignore=400)


def _build_actions_from(records):
    index = config.MyConfig.index
    doc_type = config.MyConfig.doc_type
    for record in records:
        if record["source"] == "examples":
            print("Example record ... _id={sha1} timestamp={timestamp}".format(**record))
        yield {
            "_op_type": "index",
            "_index": index,
            "_type": doc_type,
            "_id": record["sha1"],
            "_source": record
        }


def _bulk_index(client, records):
    print("Indexing new records", file=sys.stderr)
    ok, errors = elasticsearch.helpers.bulk(client, _build_actions_from(records))
    print("Uploaded successfully: {:d}".format(ok), file=sys.stderr)
    print("Errors: {}".format(errors), file=sys.stderr)


def index_records(es, records_generator):
    _create_index(es)
    _bulk_index(es, records_generator)


def lambda_handler(event, context):
    event_data = event['Records'][0]
    bucket_name = event_data['s3']['bucket']['name']
    object_key = urllib.parse.unquote_plus(event_data['s3']['object']['key'])
    full_name = "s3://{}/{}".format(bucket_name, object_key)
    print("Event: eventSource={}, eventName={}, bucket_name={}, object_key={}".format(
        event_data['eventSource'], event_data['eventName'], bucket_name, object_key))

    processed = _load_records_using(load_remote_content, full_name)
    es = _connect_to_es(aws_auth())
    print("Time remaining (ms):", context.get_remaining_time_in_millis())

    index_records(es, processed)
    print("Time remaining (ms):", context.get_remaining_time_in_millis())


def main():
    if len(sys.argv) < 2:
        print("Usage: {} LOGFILE [LOGFILE ...]".format(sys.argv[0]))
        exit(1)
    processed = load_records(sys.argv[1:])
    # If you have enabled your IP address to have access, skip the authentication:
    es = _connect_to_es(None)
    # If you have only specific users (and roles) permitted:
    # es = _connect_to_es(aws_auth())
    index_records(es, processed)


if __name__ == "__main__":
    main()