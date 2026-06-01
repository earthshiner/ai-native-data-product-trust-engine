"""Shared object filters for generated trust-engine SQL."""

from __future__ import annotations


def backup_object_exclusion_sql(column_ref: str) -> str:
    """Return a SQL predicate that excludes SHIPS/deployer backup objects.

    SHIPS table replacement preserves rollback copies using names such as
    ``Customer_bkp_20260418143022``. Older examples also use ``_bk_``.
    These are operational artefacts, not data product contract objects, so
    structural, semantic, performance and operational rules should ignore them.
    """
    trimmed = f"UPPER(TRIM({column_ref}))"
    return (
        f"{trimmed} NOT LIKE '%\\_BKP\\_%' ESCAPE '\\'\n"
        f"      AND {trimmed} NOT LIKE '%\\_BK\\_%' ESCAPE '\\'"
    )
