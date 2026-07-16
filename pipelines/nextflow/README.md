# Nextflow orchestration (draft)

A thin Nextflow layer over the Python assembly-metadata pipeline. The science
stays in the Python package (`pipeline.py`, `fetch.py`, …); Nextflow only fans
an accession list out across processes and gathers the results — parallelism,
retries and `-resume` for free, replacing the hand-rolled loop in `run_batch`.

## Layout

```
pipelines/nextflow/
├── main.nf                        # workflow: accessions channel → extract → merge
├── nextflow.config                # params (accessions, conda env, forks, gemma)
└── modules/
    ├── extract_metadata.nf        # one process = one accession (calls pipeline.py)
    └── merge_results.nf           # collect per-accession JSON → results.json
```

This deliberately mirrors ensembl-genomio's `pipelines/nextflow/`
(`workflows/`, `modules/`, `conf/`) so it can later move there with minimal
renaming.

## Run

Prerequisites: Nextflow (`curl -s https://get.nextflow.io | bash`) and the
`ensembl` conda env with `requirements.txt` installed.

```bash
cd pipelines/nextflow

# rule + vector only (fast); default accessions.txt at repo root
nextflow run main.nf

# a specific list, more papers per accession, Gemma layer on
nextflow run main.nf --accessions ../../accessions.txt --max_results 5 --gemma_enabled 1

# resume after an interruption — completed accessions are cached
nextflow run main.nf -resume
```

Outputs land in `pipelines/nextflow/results/`: `per_accession/<ACC>.json`, a
merged `results.json`, plus `timeline.html` / `report.html`.

## Key params (see `nextflow.config`)

| param           | default          | purpose                                  |
|-----------------|------------------|------------------------------------------|
| `accessions`    | `../../accessions.txt` | input list (one per line, `#` comments)  |
| `conda_env`     | `ensembl`        | env with the pipeline's dependencies     |
| `gemma_enabled` | `0`              | `1` also runs the local Gemma layer      |
| `max_results`   | `3`              | papers fetched per accession             |
| `max_forks`     | `3`              | concurrent NCBI / Europe PMC calls (rate limit) |

## How this maps onto ensembl-genomio

1. **Package the Python first.** Move the root modules under
   `src/ensembl/io/genomio/metadata/` and expose a console entry point; the
   process script then calls that instead of `python pipeline.py`.
2. **One process per accession** (`EXTRACT_METADATA`) — already the shape here.
3. **API politeness as process policy** — `maxForks` for concurrency,
   `errorStrategy 'retry'` for transient NCBI 500s (both set here).
4. **Gemma as an optional stage** — gated by `params.gemma_enabled`; keep the
   GGUF model out of the repo (container or mounted path).
5. **Collection** — `MERGE_RESULTS` (`collectFile`-style) produces the combined
   output, matching the current `robustness_summary.csv` / results JSON.

## Status

Draft for validating the orchestration shape in this repo. Not yet aligned to
ensembl-genomio's `conf/base.config` / `meta.yaml` conventions — that comes when
the Python side is packaged.
