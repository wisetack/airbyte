#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#

from source_athena.source import SourceAthena


def test_streams(mocker):
    source = SourceAthena()
    config_mock = {
        "region": "us-east-1",
        "credentials": {
            "credentials_title": "IAM User",
            "aws_access_key_id": "aws_access_key",
            "aws_secret_access_key": "aws_secret_key",
        },
        "work_groups": ["work_groups"],
    }
    streams = source.streams(config_mock)
    expected_streams_number = 1
    assert len(streams) == expected_streams_number
