# Recovery provenance records

These historical records identify the system-card and service configurations used during development. Current installation and recovery prerequisites are described in [the reproduction guide](../../../docs/reproduction.md).

| Record | Purpose |
|---|---|
| `spare_clone_record.json` | Full-image readback identity for the spare card |
| `spare_boot_smoke_record.json` | Spare-card boot and legacy image/video/camera checks |
| `legacy_service_rollback_record.json` | Original service state and rollback validation |
| `original_card_restore_record.json` | Original-card restoration record |
| `physical_card_round_trip_record.json` | Physical original/spare-card round-trip evidence |

The verified image readback was 31,914,983,424 bytes and matched the original image SHA-256. System images and raw local transcripts are excluded from this submission because they contain machine configuration and credentials. References to external original records are provenance references, not runtime inputs.

The required legacy application releases are retained in `board/recovery/releases/`. Current runtime checks also use the board identity and service snapshots under `runtime/board/`; these records do not replace those dependencies or establish the present board state.
