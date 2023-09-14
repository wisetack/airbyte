#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#


import logging
from airbyte_cdk.sources.streams.core import StreamData
import requests
import pendulum

from typing import (
    Any,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Tuple,
)
from pendulum import Date
from dacite import from_dict
from dataclasses import dataclass
from airbyte_cdk.models import SyncMode

from airbyte_cdk.sources import AbstractSource
from airbyte_cdk.sources.streams import Stream, IncrementalMixin
from airbyte_cdk.sources.streams.http import HttpStream
from airbyte_cdk.sources.streams.http.auth import NoAuth

from .helpers import get_zipcodes, chunk_date_range

BASE_URL = "http://api.weatherstack.com/"
logger = logging.getLogger("airbyte")


@dataclass
class Config:
    query: str
    access_key: str
    start_date: str
    interval: int = 24
    units: str = "f"
    is_paid_account: bool = False
    all_zip_codes: bool = False
    country_codes: Optional[List[str]] = None


class WeatherstackStream(HttpStream):
    url_base = BASE_URL
    primary_key = None

    def __init__(
        self,
        config: Config,
        **kwargs,
    ):
        super().__init__()
        self.zip_code_cursor = 0
        self.zip_codes = []
        self.config = config

    def _get_next_zip_code_chunk(self) -> List[str]:
        if self.config.all_zip_codes and not self.zip_codes:
            logger.info("Fetching all zip codes")
            self.zip_codes = list(
                get_zipcodes(
                    country_codes=self.config.country_codes,
                )
            )
            logger.info(f"Retrieved {len(self.zip_codes)} zip codes")

        per_chunk = 1000
        chunk_end = min(self.zip_code_cursor + per_chunk, len(self.zip_codes))
        chunk = self.zip_codes[self.zip_code_cursor:chunk_end]
        self.zip_code_cursor = chunk_end
        return chunk

    def request_params(
        self,
        stream_state: Mapping[str, Any],
        stream_slice: Mapping[str, Any] = None,
        next_page_token: Mapping[str, Any] = None,
    ) -> MutableMapping[str, Any]:
        # The api requires that we include access_key
        # as a query param so we do that in this method
        return {
            "query": ";".join(
                self._get_next_zip_code_chunk(),
            ) if self.config.all_zip_codes else self.config.query,
            "access_key": self.config.access_key,
            "interval": self.config.interval,
            "units": self.config.units,
            **(stream_slice or {}),
            **(next_page_token or {}),
        }

    def backoff_time(self, response: requests.Response) -> Optional[float]:
        delay_time = response.headers.get("Retry-After")
        if delay_time:
            return int(delay_time)

    def parse_response(
        self,
        response: requests.Response,
        stream_state: Mapping[str, Any],
        stream_slice: Mapping[str, Any] = None,
        next_page_token: Mapping[str, Any] = None,
    ) -> Iterable[Mapping]:
        path = self.path(stream_state, stream_slice, next_page_token)
        records = response.json()

        if not self.config.is_paid_account or not isinstance(records, list):
            records = [records]

        for record in records:
            location = record.get("location", {})
            results = record.get(path, {})
            if path in ["current"]:
                results = [results]
            else:
                results = results.values()

            for result in results:
                yield {"location": location, **result}

    def next_page_token(
        self,
        response: requests.Response,
    ) -> Optional[Mapping[str, Any]]:
        # The API does not offer pagination,
        # so we return None to indicate there are no more pages in the response
        if self.config.all_zip_codes \
                and self.zip_code_cursor < len(self.zip_codes):
            zip_code_chunk = self._get_next_zip_code_chunk()
            logger.info(f"Fetching {len(zip_code_chunk)} zip codes.")
            return {"query": ";".join(zip_code_chunk)}

        return None

    @property
    def availability_strategy(self):
        return None


class Current(WeatherstackStream):
    def path(
        self,
        stream_state: Mapping[str, Any] = None,
        stream_slice: Mapping[str, Any] = None,
        next_page_token: Mapping[str, Any] = None,
    ) -> str:
        # The "/current" path gives us the latest current weather for query
        return "current"


class Forecast(WeatherstackStream):
    def path(
        self,
        stream_state: Mapping[str, Any] = None,
        stream_slice: Mapping[str, Any] = None,
        next_page_token: Mapping[str, Any] = None,
    ) -> str:
        # The "/current" path gives us the latest current city weather
        return "forecast"


class LocationLookup(WeatherstackStream):
    def path(
        self,
        stream_state: Mapping[str, Any] = None,
        stream_slice: Mapping[str, Any] = None,
        next_page_token: Mapping[str, Any] = None,
    ) -> str:
        return "autocomplete"

    def parse_response(
        self,
        response: requests.Response,
        stream_state: Mapping[str, Any],
        stream_slice: Mapping[str, Any] = None,
        next_page_token: Mapping[str, Any] = None,
    ) -> Iterable[Mapping]:
        return [response.json()]


class IncrementalWeatherstackStream(WeatherstackStream, IncrementalMixin):
    _cursor = {}

    @property
    def state(self) -> Mapping[str, Any]:
        return {"cursor": self._cursor}

    @state.setter
    def state(self, value: Mapping[str, Any]):
        cursor = value.get("cursor", {})
        state_date = cursor.get(self.cursor_field) or self.config.start_date
        cursor_date = self._cursor.get(self.cursor_field) \
            or self.config.start_date
        self._cursor = {
            "zipcodes": cursor.get("zipcodes", []),
            self.cursor_field: max(state_date, cursor_date),
        }

    def read_records(
        self,
        sync_mode: SyncMode,
        cursor_field: Optional[List[str]] = None,
        stream_slice: Optional[Mapping[str, Any]] = None,
        stream_state: Optional[Mapping[str, Any]] = None,
    ) -> Iterable[StreamData]:
        for record in super().read_records(
            sync_mode,
            cursor_field,
            stream_slice,
            stream_state,
        ):
            yield record

            cursor_date = self._cursor.get(
                self.cursor_field,
                self.config.start_date,
            )
            record_date = record.get(
                self.cursor_field,
                self.config.start_date,
            )
            self._cursor = {
                **self._cursor,
                self.cursor_field: max(record_date, cursor_date),
            }

    def stream_slices(
        self,
        *,
        sync_mode: SyncMode,
        cursor_field: Optional[List[str]] = None,
        stream_state: Optional[Mapping[str, Any]] = None,
    ) -> Iterable[Optional[Mapping[str, Any]]]:
        is_first_run = not stream_state
        default_start_date = pendulum.parse(
            self.config.start_date,
            exact=True,
        )

        state = (stream_state or {}).get("cursor", {})
        cursor_date = state.get(self.cursor_field)
        cursor_start_date: Optional[Date] = None
        if cursor_date:
            cursor_start_date = pendulum.parse(
                state.get(self.cursor_field),
                exact=True,
            )

        start_date = cursor_start_date or default_start_date
        if self.config.all_zip_codes:
            zip_codes = stream_state.get("zipcodes", [])
            all_zip_codes = get_zipcodes(
                country_codes=self.config.country_codes,
            )

            new_zip_codes = list(set(all_zip_codes) - set(zip_codes))

            if len(new_zip_codes) > 0 and not is_first_run:
                date_chunks = chunk_date_range(
                    default_start_date,
                    pendulum.duration(days=60),
                )
                for chunk in date_chunks:
                    yield {
                        "query": ";".join(new_zip_codes),
                        "historical_date_start": chunk.start.to_date_string(),
                        "historical_date_end": chunk.end.to_date_string(),
                    }

            date_chunks = chunk_date_range(
                start_date,
                pendulum.duration(days=60),
            )
            for chunk in date_chunks:
                yield {
                    "query": ";".join(new_zip_codes),
                    "historical_date_start": chunk.start.to_date_string(),
                    "historical_date_end": chunk.end.to_date_string(),
                }

            self._cursor = {
                **self._cursor,
                "zipcodes": all_zip_codes,
            }

        else:
            date_chunks = chunk_date_range(
                start_date,
                pendulum.duration(days=60),
            )
            for chunk in date_chunks:
                yield {
                    "historical_date_start": chunk.start.to_date_string(),
                    "historical_date_end": chunk.end.to_date_string(),
                }


class Historical(IncrementalWeatherstackStream):
    cursor_field = "date"

    def path(
        self,
        stream_state: Mapping[str, Any] = None,
        stream_slice: Mapping[str, Any] = None,
        next_page_token: Mapping[str, Any] = None,
    ) -> str:
        return "historical"


class SourceWeatherstack(AbstractSource):
    def check_connection(self, logger, config) -> Tuple[bool, any]:
        try:
            query = config.get("query", "20001")
            access_key = config["access_key"]

            response = requests.get(
                f"{BASE_URL}/current?access_key={access_key}&query={query}",
            )
            response = response.text

            if response.find('"success": false') != -1:
                return False, "Check Query and Access Key"
            else:
                return True, None
        except requests.exceptions.RequestException as e:
            return False, repr(e)

    def streams(self, config: Mapping[str, Any]) -> List[Stream]:
        auth = NoAuth()
        parsed_config = from_dict(data_class=Config, data=config)

        streams = [
            Current(authenticator=auth, config=parsed_config),
            Forecast(authenticator=auth, config=parsed_config),
        ]

        # Historical stream is only supported by paid accounts
        if parsed_config.is_paid_account:
            streams.extend(
                [
                    LocationLookup(
                        authenticator=auth,
                        config=parsed_config,
                    ),
                    Historical(
                        authenticator=auth,
                        config=parsed_config,
                    ),
                ]
            )

        return streams
