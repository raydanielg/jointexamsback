"""Numeric ordering for candidate numbers.

Candidate numbers carry prefixes (``JNT/2026/0001``, ``ORG-X-0001``, ``C-01``);
sorting the raw string orders by prefix, not by value. ``numeric_suffix``
extracts the trailing digits so rows sort by the actual sequence.
"""

from django.db import connection
from django.db.models import F, Func, IntegerField, Value
from django.db.models.functions import Cast


def numeric_suffix(field: str = "candidate_number"):
    if connection.vendor == "postgresql":
        return Cast(
            Func(
                F(field),
                # the non-digit before the final digit run stops `.*` from
                # greedily consuming all but the last digit
                Value(r".*[^0-9]([0-9]+)$"),
                Value(r"\1"),
                function="regexp_replace",
            ),
            IntegerField(),
        )
    # Other engines (SQLite tests) lack regexp_replace — fall back to the raw
    # string; zero-padded numbers still order correctly per prefix.
    return F(field)
