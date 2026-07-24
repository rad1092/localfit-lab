# Data Lineage Contract

## Protected canonical roots

The product consumes, but does not own, the workspace data and research pipeline.

| Purpose | Default root | Override |
| --- | --- | --- |
| Source and derived datasets | `../datacorpus` | `LOCALFIT_DATA_ROOT` |
| Research papers and source material | `../research` | `LOCALFIT_RESEARCH_ROOT` |
| Private collection credentials | `../docs/90_private/key.md` | `LOCALFIT_KEY_FILE` |
| Curated runtime evidence | `resources/knowledge/rag_sources` | `LOCALFIT_KNOWLEDGE_ROOT` |

The private key file stays outside `final_proj`, is excluded from Git, and must never be copied into frontend assets, logs, manifests, or reports.

## Processing lineage

```text
source files + _raw_ingest
  -> collection manifests and checksums
  -> _processed
  -> _silver
  -> _gold
  -> _gold_validation and _rule_validation
  -> _score_backtest and _location_judgement_outputs
  -> _final/model_ready
  -> runtime/db/commercial.db
  -> location analysis, AI reports, and the assistant
```

## Required trace fields

Every production input should be traceable by these fields in inventory or validation artifacts:

- artifact path and layer
- upstream input paths
- producer script
- grain and join keys
- source period and build timestamp
- row count and SHA-256 where available
- validation artifact
- service table or feature that consumes it

## Change rule

Files under the canonical roots are never removed as part of product-folder cleanup. A copied dataset inside `final_proj` may be removed only after its canonical counterpart is hash-verified, any unique artifact is preserved, and database rebuild plus product regression checks pass.

## 2026-07-10 product-copy reconciliation

- Compared 75 files from the former `final_proj/data/datacorpus` mirror against the canonical `../datacorpus` root.
- 74 files matched their canonical counterpart by relative path, byte size, and SHA-256.
- Preserved the only unique artifact at `../datacorpus/_location_judgement_outputs/loc_score_v2_batch_20261_20260708_075336.csv`.
- Preserved artifact SHA-256: `EBCC05FEA4C199D8B43B72CB7426729E44F7FC87468E1B68099D3E79171C383D`.
