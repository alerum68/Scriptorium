# Scaffold Data Contract & Master DB Schema

This specification details the Master DB JSON format and scaffold sheet contract shared between `Voyageur` (gather stage) and `Paleographer` (analyze stage).

---

## Master DB JSON Schema

The Master DB JSON is the primary intermediate data format used across Antiquarian. It represents a single archival collection.

```json
{
  "collection_title": "Assumption Parish Register 1845-1860",
  "record_type_name": "Parish",
  "sheets": [
    {
      "page_id": "page_001.jpg",
      "document_metadata": {
        "file_name": "page_001.jpg",
        "file_type": "jpg",
        "repository": "Canadiana",
        "reel_number": "C-12345"
      },
      "records": [
        {
          "event_type": "Baptism",
          "event_date": "1850-05-12",
          "event_place": "Assumption Parish, Pembina",
          "participants": [
            {
              "role_name": "Primary",
              "std_given": "Joseph",
              "std_surname": "Grant",
              "verbatim_name": "Joseph Grant",
              "sex": "M"
            }
          ]
        }
      ]
    }
  ]
}
```

---

## Scaffold Sheet Format

When Voyageur downloads a microfilm roll, volume, or census district, it creates a scaffold Master DB JSON before any automated extraction takes place.

A **scaffold sheet** contains document metadata but leaves `records` empty:

```json
{
  "page_id": "page_002.jpg",
  "document_metadata": {
    "file_name": "page_002.jpg",
    "file_type": "jpg",
    "repository": "Canadiana"
  },
  "records": []
}
```

### Why Scaffolds Exist
- **Deduplication**: Prevents duplicate downloads when re-running gather tasks.
- **Ordering**: Preserves the original archival page sequence regardless of processing order.
- **Progress Tracking**: Enables Paleographer to identify unanalyzed sheets (`records` is empty) versus analyzed sheets (`records` is populated).

---

## Master DB Merge Lifecycle

When Paleographer transcribes an image, `save_master_db()` merges the analyzed sheet into the Master DB:

1. **Matching**: Matches the analyzed sheet against existing sheets in `MasterDB_<collection>.json` using `page_id` or `file_name`.
2. **Replacement**: Replaces the empty scaffold sheet with the fully populated analyzed sheet.
3. **Appends**: If no matching scaffold exists (e.g., in standalone image analysis), appends the new sheet to the end of `sheets`.
4. **Validation**: Calls `Commissioner.record_registry.validate_soft()` before dumping updated JSON to disk.

---

## Numeric Field Normalization

Before a Voyageur gatherer writes a payload to disk (a standalone gather file in `A.py`/`FS.py`, or a checkpointed Master DB save in `HBCA.py`/`LAC.py`), `Commissioner.textutils.dynamic_zero_pad_all_except` walks the whole payload and zero-pads numeric-bearing fields (e.g. `family_number`, `line_number`, `claim_number`) to the widest value seen anywhere in that write, so lexicographic sort matches numeric order.

This is an exclusion list, not an include list — new ID/reference fields get padded automatically. Fields exempted because padding would break matching, routing, or archival convention (not exhaustive; see `_PAD_EXCLUDED_FIELDS` in `Commissioner/textutils.py`):

- **Matching keys**: `page_id`, `file_name`, `image_id`, `item_id` — padding would break the exact-match lookups the Merge Lifecycle above and download-resume dedup rely on.
- **Routing/path fields**: `enumeration_district` (feeds `Archivist/Census.py`'s image directory path), `rg_series_code`, `commission_reference` (matched by exact substring in `Archivist/Scrip.py`'s template routing).
- **Citation fields rendered unpadded by convention**: `folio`, `volume`, `reel_numbers`.
- **Dates, amounts, names, and free text** generally.

---

## Download Checkpoints

Voyageur tracks gather progress in a `.checkpoint` JSON file stored alongside downloaded images:

```json
{
  "reel_number": "C-12345",
  "completed_pids": ["pid_001", "pid_002"],
  "failed_pids": []
}
```

If a download job is interrupted, Voyageur reads `.checkpoint` on restart and skips already-downloaded page assets.
