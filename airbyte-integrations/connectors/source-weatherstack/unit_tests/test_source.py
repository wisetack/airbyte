#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#

from source_weatherstack.source import SourceWeatherstack


def test_streams(mocker):
    source = SourceWeatherstack()
    config_mock = {
        "access_key": "hello",
        "query": "20001",
        "start_date": "2020-01-01",
        "is_paid_account": False,
    }
    streams = source.streams(config_mock)
    expected_streams_number = 2
    assert len(streams) == expected_streams_number


def test_streams_is_paid_account(mocker):
    source = SourceWeatherstack()
    config_mock = {
        "access_key": "hello",
        "query": "20001",
        "start_date": "2020-01-01",
        "is_paid_account": True,
    }
    streams = source.streams(config_mock)
    expected_streams_number = 4
    assert len(streams) == expected_streams_number
