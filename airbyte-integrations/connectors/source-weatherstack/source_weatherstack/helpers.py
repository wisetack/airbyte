#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#

import csv
import pendulum

from io import StringIO
from typing import List, Mapping, Optional, Iterable
from urllib.request import urlretrieve
from zipfile import ZipFile
from pendulum import Date, Period

DEFAULT_COUNTRY_CODES = [
    "US",
    "PR",
    "VI",
    "AS",
    "MP",
    "GU",
    "WK",
    "CA",
    "UK",
]


def chunk_date_range(
    start_date: Date,
    interval=pendulum.duration(days=1),
    end_date: Optional[Date] = None,
) -> Iterable[Period]:
    """
    Yields a list of the beginning and ending timestamps
    of each day between the start date and now.
    The return value is a pendulum.period
    """
    end_date = end_date or pendulum.now("UTC").date()

    chunk_start_date = start_date
    while chunk_start_date < end_date:
        chunk_end_date = min(chunk_start_date + interval, end_date)
        yield pendulum.period(chunk_start_date, chunk_end_date)
        chunk_start_date = chunk_end_date


#######################################
# Zip code data
#######################################
def _filter_country(data, country_code) -> List[Mapping[str, str]]:
    subset = []
    for entry in data:
        if entry[0] == country_code:
            subset.append(entry)

    return subset


def get_zipcodes(
    country_codes: Optional[List[str]] = None,
) -> Iterable[str]:
    url = "http://download.geonames.org/export/zip/allCountries.zip"

    if not country_codes:
        country_codes = DEFAULT_COUNTRY_CODES

    filehandle, _ = urlretrieve(url)
    zip_file_object = ZipFile(filehandle, "r")
    first_file = zip_file_object.namelist()[0]
    fn = zip_file_object.open(first_file)
    content = fn.read()

    data = []
    d = StringIO(content.decode())
    reader = csv.reader(d, delimiter="\t")
    for line in reader:
        data.append(line)

    for country_code in country_codes:
        subset = _filter_country(data, country_code)
        for entry in subset:
            yield entry[1]
