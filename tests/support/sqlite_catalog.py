from __future__ import annotations

import sqlite3


DEFAULT_FILTER_RULES_JSON = '{"preset":"ALL_USER_FILES","schema_version":1}'
DEFAULT_FILTER_RULES_HASH = (
    "5b551f66adfe79a9e025a369c44e76ece00928588f965a93fe6cdcfbdb1e4a9b"
)


def insert_default_filter_set_version(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    filter_set_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO filter_set_versions (
            job_id,
            filter_set_id,
            version,
            rules_hash,
            rules_json
        )
        VALUES (?, ?, 1, ?, ?)
        """,
        (
            job_id,
            filter_set_id,
            DEFAULT_FILTER_RULES_HASH,
            DEFAULT_FILTER_RULES_JSON,
        ),
    )
