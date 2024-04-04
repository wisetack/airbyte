import enum
import boto3
from logging import Logger
from typing import List, Iterator, Optional
from dataclasses import dataclass


class CredentialsType(enum.Enum):
    IAM_ROLE = "IAM Role"
    IAM_USER = "IAM User"


@dataclass
class Credentials:
    credentials_title: str
    type: Optional[CredentialsType] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    role_arn: Optional[str] = None

    def __post_init__(self):
        self.type = CredentialsType(self.credentials_title)


@dataclass
class ConnectorConfig:
    region: str
    credentials: Credentials
    work_groups: List[str]

    def __post_init__(self):
        self.credentials = Credentials(**self.credentials)


class AthenaHandler:
    def __init__(
        self,
        logger: Logger,
        config: ConnectorConfig,
    ) -> None:
        self._logger = logger
        self._session: boto3.Session = None
        self._config: ConnectorConfig = config

        self._create_session()
        self._client = self._session.client("athena")
        logger.info("Athena client created successfully.")

    def _create_session(self) -> None:
        if self._config.credentials.type == CredentialsType.IAM_USER:
            self._session = boto3.Session(
                aws_access_key_id=self._config.credentials.aws_access_key_id,
                aws_secret_access_key=self._config.credentials.aws_secret_access_key,
                region_name=self._config.region,
            )

        elif self._config.credentials.type == CredentialsType.IAM_ROLE:
            client = boto3.client("sts")
            role = client.assume_role(
                RoleArn=self._config.credentials.role_arn,
                RoleSessionName="airbyte-source-athena",
            )
            creds = role.get("Credentials", {})
            self._session = boto3.Session(
                aws_access_key_id=creds.get("AccessKeyId"),
                aws_secret_access_key=creds.get("SecretAccessKey"),
                aws_session_token=creds.get("SessionToken"),
                region_name=self._config.region,
            )

    def list_all_execution_ids(self) -> Iterator[List[str]]:
        batch = 50
        fetch = True
        next_token = None

        for workgroup in self._config.work_groups:
            self._logger.info(
                f"Fetching query executions for workgroup: {workgroup}",
            )
            while fetch:
                if next_token:
                    executions = self._client.list_query_executions(
                        WorkGroup=workgroup,
                        MaxResults=batch,
                        NextToken=next_token,
                    )
                else:
                    executions = self._client.list_query_executions(
                        WorkGroup=workgroup,
                        MaxResults=batch,
                    )

                ids = executions.get("QueryExecutionIds", [])
                next_token = executions.get("NextToken")
                yield ids

                if len(ids) < 1 or not next_token:
                    fetch = False

    def fetch_query_executions(self, executions_ids: List[str]) -> List[dict]:
        query = self._client.batch_get_query_execution(
            QueryExecutionIds=executions_ids,
        )
        return query.get("QueryExecutions", [])
