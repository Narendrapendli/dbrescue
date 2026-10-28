# Data Integrity Engine & Canonical Serialization Specification (`docs/INTEGRITY.md`)

This document defines the mathematical and cryptographic specification of the `dbrescue` data integrity verification system, including canonical serialization, Unicode NFC normalization, row-level SHA-256 hashing, dataset fingerprinting, and granular discrepancy detection.

---

## 1. Overview & Architectural Goals

The integrity engine answers the central question of disaster recovery:
> **"Can we mathematically prove that the recovered database contains the exact same data as the pre-disaster state down to the byte level?"**

Standard row counts (`SELECT COUNT(*)`) are insufficient because:
1. They cannot detect modified or overwritten rows (silent data corruption).
2. They cannot detect swapped values or inverted records.
3. Database storage layout and insertion order are non-deterministic.

`dbrescue` solves this by computing a **deterministic composite cryptographic fingerprint** over the canonicalized byte representation of every row, ordered by primary key.

---

## 2. Canonical Serialization Algorithm

To prevent hashing discrepancies caused by serialization ambiguities (such as field delimiter collisions or differing string encodings), every field value is encoded into a strictly formatted, type-tagged, length-prefixed byte stream:

$$\text{Field} = \text{TypeTag} : \text{LengthInBytes} : \text{EncodedBytes}$$

### 2.1 Type Normalization Table

| SQL / Python Type | Type Tag | Normalization & Encoding Rules | Example Representation |
| :--- | :--- | :--- | :--- |
| **`NULL` (`None`)** | `N` | Fixed 0 length: `N:0:` | `b"N:0:"` |
| **`BOOLEAN` (`bool`)** | `B` | `B:1:1` for True, `B:1:0` for False | `b"B:1:1"` |
| **`INTEGER` (`int`)** | `I` | Base-10 ASCII string, length-prefixed | `b"I:3:101"` |
| **`DECIMAL` (`Decimal`)** | `D` | Normalized decimal string (no scientific notation, trailing zeros stripped where appropriate) | `b"D:4:3.95"` |
| **`FLOAT` (`float`)** | `F` | Normalized base-10 floating-point string | `b"F:4:3.14"` |
| **`DATETIME` / `DATE`** | `T` | ISO-8601 UTC string (`YYYY-MM-DDTHH:MM:SS`) | `b"T:19:2026-10-28T22:30:00"` |
| **`VARCHAR` / `TEXT`** | `S` | **Unicode NFC normalized** (`unicodedata.normalize('NFC', ...)`), UTF-8 encoded | `b"S:4:Alex"` |
| **`BLOB` / `VARBINARY`**| `Y` | Raw byte sequence with length prefix | `b"Y:4:\x01\x02\x03\x04"` |

### 2.2 Delimiter Collision Prevention

Consider two string columns:
- Case A: `Col1 = "a"`, `Col2 = "bc"`
- Case B: `Col1 = "ab"`, `Col2 = "c"`

In naïve delimiter-based systems (e.g. `col1 + "|" + col2`), both produce `"a|bc"` if delimiters are escaped or missing.
Under `dbrescue` length prefixing:
- Case A: `C:4:Col1` + `S:1:a` + `C:4:Col2` + `S:2:bc`
- Case B: `C:4:Col1` + `S:2:ab` + `C:4:Col2` + `S:1:c`
The serialized byte streams are completely distinct.

---

## 3. Cryptographic Fingerprinting Workflow

```mermaid
flowchart TD
    RawRows[Raw Database Rows] --> PKOrder[Sort Rows by Primary Key: (ID ASC)]
    
    subgraph RowProcessing ["Per-Row Processing"]
        PKOrder --> Canon[Canonical Serialization: TypeTag + Len + Bytes]
        Canon --> RowHash["Compute Row SHA-256 Hash<br/>h_i = SHA256(canonical_row_i)"]
    end
    
    RowHash --> HashStream[Stream of Ordered Row Hashes: h_1, h_2, ..., h_n]
    HashStream --> CompHasher["Composite Hasher<br/>Update with h_1 || h_2 || ... || h_n"]
    CompHasher --> TableFingerprint[Overall Table Fingerprint SHA-256]
```

1. **Ordering Independence**: Rows are retrieved or sorted strictly by primary key tuple $(PK_1, PK_2, \dots)$. No matter which order the database engine returns the rows, the resulting sequence is identical.
2. **Row-Level SHA-256**:
   $$h_i = \text{SHA-256}(\text{serialize\_row}(\text{columns}, \text{row}_i))$$
3. **Composite Table Fingerprint**:
   $$\text{Fingerprint} = \text{SHA-256}(h_1 \parallel h_2 \parallel \dots \parallel h_n)$$

---

## 4. Worked Example: Step-by-Step

Consider two student records in table `students`:

```text
Row 1: (id=1, name="Alex", gpa=Decimal("3.85"), enrolled=date(2023, 9, 1))
Row 2: (id=2, name="Jordan", gpa=Decimal("3.92"), enrolled=date(2023, 9, 1))
```

### Step 1: Canonical Serialization of Row 1

Column names and values are paired and length-prefixed:
```text
C:2:id         -> b"C:2:id"
I:1:1          -> b"I:1:1"
C:4:name       -> b"C:4:name"
S:4:Alex       -> b"S:4:Alex"
C:3:gpa        -> b"C:3:gpa"
D:4:3.85       -> b"D:4:3.85"
C:8:enrolled   -> b"C:8:enrolled"
T:10:2023-09-01 -> b"T:10:2023-09-01"
```

Combined byte stream for Row 1:
```text
b"C:2:idI:1:1C:4:nameS:4:AlexC:3:gpaD:4:3.85C:8:enrolledT:10:2023-09-01"
```

### Step 2: Compute Row Hashes

- $\text{Row}_1\text{ Hash} = \text{SHA-256}(\text{Row}_1\text{ Bytes})$
  `c93bb9e2df8d7951a37c0eb8d7a174092ee0fc882fa4340798a3c8e47c1b5049`
- $\text{Row}_2\text{ Hash} = \text{SHA-256}(\text{Row}_2\text{ Bytes})$
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

### Step 3: Compute Overall Dataset Fingerprint

Combine sorted row hashes and hash again:
$$\text{Dataset Fingerprint} = \text{SHA-256}(h_1 \parallel h_2)$$

---

## 5. Granular Discrepancy Detection & Diffing

When comparing a recovered database state against a baseline snapshot, `compare_datasets` produces a typed `TableComparisonResult`:

```python
TableComparisonResult(
    table_name="students",
    baseline_count=100,
    target_count=100,
    count_match=True,
    baseline_fingerprint="a1b2c3...",
    target_fingerprint="d4e5f6...",
    fingerprint_match=False,
    missing_ids=[],
    unexpected_ids=[],
    changed_ids={
        14: {
            "gpa": (Decimal("3.88"), Decimal("3.20"))  # Exact column delta!
        }
    },
    reasons=[
        "Dataset fingerprint mismatch: baseline=a1b2..., target=d4e5...",
        "Found 1 modified records with column-level value discrepancies"
    ],
    passed=False  # Requires ALL checks to be clean
)
```

### Discrepancy Matrix:
- **Missing IDs**: Primary keys present in baseline but absent in target (deleted records).
- **Unexpected IDs**: Primary keys present in target but absent in baseline (phantom rows or rogue inserts).
- **Changed IDs**: Rows with matching primary keys whose column values differ. The engine outputs the exact `(old_value, new_value)` pair for each altered column.
- **Pass Rule**: `count_match AND fingerprint_match AND no_missing AND no_unexpected AND no_changed`.
