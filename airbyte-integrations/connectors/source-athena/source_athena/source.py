#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#

from abc import ABC
from datetime import datetime
from logging import getLogger, Logger
from typing import (
    Any,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Tuple,
    Union,
)
from logging import CRITICAL
from sqlglot.errors import ErrorLevel
from sqlglot import parse_one, exp, logger
from sqlglot.dialects.athena import Athena

from airbyte_cdk.sources import AbstractSource
from airbyte_cdk.sources.streams import Stream, IncrementalMixin
from airbyte_cdk.models import (
    AirbyteMessage,
    AirbyteRecordMessage,
    SyncMode,
    Type,
)

from .helpers import AthenaHandler, ConnectorConfig

# suppress excessive logging by sqlglot
logger.setLevel(CRITICAL)


class AthenaStream(Stream, ABC):
    def __init__(self, config: Mapping[str, any]) -> None:
        super().__init__()
        self._logger = getLogger("airbyte")
        self._config = ConnectorConfig(**config)
        self._handler = AthenaHandler(self._logger, self._config)

    def _process_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        query = record.get("Query")
        query_dt = record.get("Status", {}).get("SubmissionDateTime")
        statement_type = record.get("SubstatementType")

        record["TableInformation"] = []
        record["QueryDateTime"] = query_dt

        if statement_type == "SELECT" and query:
            try:
                query_info = []
                parsed_query = parse_one(
                    query,
                    dialect=Athena,
                    error_level=ErrorLevel.WARN,
                )
                for tbl in parsed_query.find_all(exp.Table):
                    query_info.append(
                        {"TableName": tbl.name, "DatabaseName": tbl.db},
                    )
                record["TableInformation"] = query_info
            except Exception as e:
                # never raise on exception, we don't want
                # to stop the stream if we can't parse the query
                self._logger.error(f"Error while parsing query: {e}")

        return record

    def stream_slices(
        self,
        **kwargs,
    ) -> Iterable[Optional[Mapping[str, Any]]]:
        yield from self._handler.list_all_execution_ids()

    def read_records(
        self,
        sync_mode: SyncMode,
        cursor_field: Optional[List[str]] = None,
        stream_slice: Optional[Mapping[str, Any]] = None,
        stream_state: Optional[Mapping[str, Any]] = None,
    ) -> Iterable[Union[Mapping[str, Any], AirbyteMessage]]:
        for record in self._handler.fetch_query_executions(stream_slice):
            yield AirbyteMessage(
                type=Type.RECORD,
                record=AirbyteRecordMessage(
                    stream=self.name,
                    data=self._process_record(record),
                    emitted_at=int(datetime.now().timestamp()) * 1000,
                ),
            )


class IncrementalAthenaStream(AthenaStream, IncrementalMixin):
    _cursor_value: Optional[datetime] = None
    _initial_cursor_value: Optional[datetime] = None
    state_checkpoint_interval = 100

    @property
    def state(self) -> MutableMapping[str, Any]:
        return {
            self.cursor_field: self._cursor_value.isoformat(),
        }

    @state.setter
    def state(self, value: MutableMapping[str, Any]) -> None:
        cursor = value.get(self.cursor_field)
        if isinstance(cursor, datetime):
            self._cursor_value = cursor
        else:
            self._cursor_value = datetime.fromisoformat(cursor)

        self._initial_cursor_value = self._cursor_value

    def read_records(
        self,
        sync_mode: SyncMode,
        cursor_field: Optional[List[str]] = None,
        stream_slice: Optional[Mapping[str, Any]] = None,
        stream_state: Optional[Mapping[str, Any]] = None,
    ) -> Iterable[Union[Mapping[str, Any], AirbyteMessage]]:
        is_incremental = self._cursor_value \
            and sync_mode == SyncMode.incremental

        for record in self._handler.fetch_query_executions(stream_slice):
            record = self._process_record(record)
            current_cursor = record.get(self.cursor_field)

            if is_incremental and current_cursor <= self._initial_cursor_value:
                continue

            self._cursor_value = max(current_cursor, self._initial_cursor_value) \
                if self._initial_cursor_value else current_cursor

            yield AirbyteMessage(
                type=Type.RECORD,
                record=AirbyteRecordMessage(
                    stream=self.name,
                    data=record,
                    emitted_at=int(datetime.now().timestamp()) * 1000,
                ),
            )


class QueryExecution(IncrementalAthenaStream):
    cursor_field = "QueryDateTime"
    primary_key = "QueryExecutionId"


class SourceAthena(AbstractSource):
    def check_connection(
        self,
        logger: Logger,
        config: Mapping[str, any],
    ) -> Tuple[bool, any]:
        try:
            AthenaHandler(logger, ConnectorConfig(**config))
            return True, None
        except Exception as error:
            logger.error(f"Failed to connect to Athena: {error}")
            return False, error

    def streams(self, config: Mapping[str, Any]) -> List[Stream]:
        return [
            QueryExecution(config),
        ]
