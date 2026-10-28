"""Unit tests for dbrescue.integrity canonical serialization, hashing, and diffing."""

from __future__ import annotations

import datetime
import unicodedata
from decimal import Decimal

import pytest

from dbrescue.integrity import (
    TableComparisonResult,
    compare_datasets,
    compute_row_hash,
    compute_table_fingerprint_from_rows,
    serialize_value,
)


@pytest.fixture
def sample_columns():
    return ["student_id", "name", "gpa", "status", "enrolled_date", "notes"]


@pytest.fixture
def sample_rows():
    return [
        (1, "Alex Smith", Decimal("3.85"), "ACTIVE", datetime.date(2023, 9, 1), None),
        (2, "Jordan Taylor", Decimal("3.92"), "ACTIVE", datetime.date(2023, 9, 1), "Honor roll"),
        (3, "Casey Johnson", Decimal("3.45"), "PROBATION", datetime.date(2023, 9, 1), None),
    ]


@pytest.mark.unit
def test_identical_data_passes(sample_columns, sample_rows):
    """Verifies that comparing two identical row sets passes all integrity checks."""
    result = compare_datasets("students", sample_columns, sample_rows, sample_rows)
    assert isinstance(result, TableComparisonResult)
    assert result.passed is True
    assert result.count_match is True
    assert result.fingerprint_match is True
    assert len(result.missing_ids) == 0
    assert len(result.unexpected_ids) == 0
    assert len(result.changed_ids) == 0
    assert len(result.reasons) == 0


@pytest.mark.unit
def test_one_missing_row(sample_columns, sample_rows):
    """Verifies that an absent row is detected in missing_ids and marks check failed."""
    target_rows = sample_rows[:2]  # Row 3 is missing
    result = compare_datasets("students", sample_columns, sample_rows, target_rows)

    assert result.passed is False
    assert result.count_match is False
    assert result.fingerprint_match is False
    assert result.missing_ids == [3]
    assert len(result.unexpected_ids) == 0
    assert any("missing records" in r for r in result.reasons)


@pytest.mark.unit
def test_one_unexpected_extra_row(sample_columns, sample_rows):
    """Verifies that an extra row is flagged in unexpected_ids and fails check."""
    extra_row = (4, "Morgan Williams", Decimal("3.78"), "ACTIVE", datetime.date(2023, 9, 1), None)
    target_rows = list(sample_rows) + [extra_row]

    result = compare_datasets("students", sample_columns, sample_rows, target_rows)
    assert result.passed is False
    assert result.count_match is False
    assert result.fingerprint_match is False
    assert result.unexpected_ids == [4]
    assert len(result.missing_ids) == 0
    assert any("unexpected records" in r for r in result.reasons)


@pytest.mark.unit
def test_one_changed_value_with_column_diff(sample_columns, sample_rows):
    """Verifies that a modified column value is detected with granular (old, new) diff."""
    # Modify Casey's GPA from 3.45 to 3.10
    target_rows = [
        sample_rows[0],
        sample_rows[1],
        (3, "Casey Johnson", Decimal("3.10"), "PROBATION", datetime.date(2023, 9, 1), None),
    ]

    result = compare_datasets("students", sample_columns, sample_rows, target_rows)
    assert result.passed is False
    assert result.count_match is True  # Same count!
    assert result.fingerprint_match is False  # Fingerprint MUST catch content change
    assert 3 in result.changed_ids
    assert result.changed_ids[3]["gpa"] == (Decimal("3.45"), Decimal("3.10"))
    assert any("modified records" in r for r in result.reasons)


@pytest.mark.unit
def test_same_count_different_content_must_fail(sample_columns, sample_rows):
    """CRITICAL: Same row count with swapped/altered values must strictly FAIL."""
    target_rows = [
        sample_rows[0],
        # ID 2 replaced with ID 99
        (99, "Fake Student", Decimal("2.00"), "SUSPENDED", datetime.date(2023, 9, 1), None),
        sample_rows[2],
    ]
    result = compare_datasets("students", sample_columns, sample_rows, target_rows)
    assert result.count_match is True  # Counts are both 3
    assert result.fingerprint_match is False  # Must detect mismatch
    assert result.passed is False
    assert 2 in result.missing_ids
    assert 99 in result.unexpected_ids


@pytest.mark.unit
def test_ordering_independence(sample_columns, sample_rows):
    """Verifies that row ordering differences produce IDENTICAL fingerprints due to PK sorting."""
    reversed_rows = list(reversed(sample_rows))
    fp1, _ = compute_table_fingerprint_from_rows(sample_columns, sample_rows)
    fp2, _ = compute_table_fingerprint_from_rows(sample_columns, reversed_rows)

    assert fp1 == fp2, "Fingerprint must be independent of database row retrieval order."

    # Direct comparison must pass
    result = compare_datasets("students", sample_columns, sample_rows, reversed_rows)
    assert result.passed is True
    assert result.fingerprint_match is True


@pytest.mark.unit
def test_unicode_nfc_normalization():
    """Verifies that NFC and NFD Unicode representations serialize to identical byte streams."""
    # e with acute accent: decomposed (e + accent) vs precomposed (é)
    nfd_str = "e\u0301"  # NFD: 2 code points
    nfc_str = "\u00e9"   # NFC: 1 code point

    assert nfd_str != nfc_str  # Python string equality is False before normalization
    assert unicodedata.normalize("NFD", nfc_str) == nfd_str

    ser_nfd = serialize_value(nfd_str)
    ser_nfc = serialize_value(nfc_str)

    assert ser_nfd == ser_nfc, "NFC and NFD string forms must produce identical serialized bytes"


@pytest.mark.unit
def test_null_value_handling():
    """Verifies that NULL/None produces unambiguous length-prefixed bytes."""
    ser_none = serialize_value(None)
    assert ser_none == b"N:0:"

    cols = ["id", "val"]
    row_with_none = (1, None)
    row_with_empty_str = (1, "")

    h_none = compute_row_hash(cols, row_with_none)
    h_empty = compute_row_hash(cols, row_with_empty_str)

    assert h_none != h_empty, "NULL and empty string must NOT produce identical row hashes."


@pytest.mark.unit
def test_decimal_normalization():
    """Verifies that Decimal values with trailing zeros normalize equivalently."""
    d1 = Decimal("3.8500")
    d2 = Decimal("3.85")

    ser1 = serialize_value(d1)
    ser2 = serialize_value(d2)

    assert ser1 == ser2, "Decimal('3.8500') and Decimal('3.85') must normalize to identical bytes."


@pytest.mark.unit
def test_determinism_across_runs(sample_columns, sample_rows):
    """Verifies that fingerprint computation is 100% deterministic across multiple iterations."""
    fp_first, _ = compute_table_fingerprint_from_rows(sample_columns, sample_rows)

    for _ in range(50):
        fp_next, _ = compute_table_fingerprint_from_rows(sample_columns, sample_rows)
        assert fp_first == fp_next
