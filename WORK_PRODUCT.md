# GSoC 2026 Work Product — Literature-based Genome-Assembly Metadata Extraction

**Contributor:** Soomin Lee
**Organization:** EMBL-EBI (Ensembl)
**Mentors:** Jorge Alvarez, Disha Lodha
**Upstream project:** [Ensembl/ensembl-genomio](https://github.com/Ensembl/ensembl-genomio)

This is my Google Summer of Code 2026 final work product. It summarises the
project goals, what was built, the current state, what remains, where the code
lives (including the upstream contribution), and the main challenges.

## Goals

Key biological metadata for a genome assembly (ploidy, sex, cultivar/strain) is
frequently missing or inconsistent in the INSDC archives, even though it is
usually stated in the assembly's publication. The goal of this project was to
build a pipeline that, given an NCBI assembly accession, retrieves the linked
publication, reads it, and extracts that metadata with an explicit evidence
trail, so it can be reviewed and loaded into Ensembl.

## What I did

- **End-to-end extraction pipeline** (`fetch` -> `parse` -> `extract` + `search`
  -> optional `gemma` -> `pipeline`):
  - **Retrieval** (`fetch.py`): resolves the assembly via NCBI Datasets, follows
    linked PMIDs / Entrez elink / BioProject, and escalates to Europe PMC name,
    full-text-species, and Entrez searches, with relevance scoring.
  - **Parsing** (`parse.py`): full-text XML, abstracts, and **supplementary
    files** (docx/xlsx/csv/html) into weighted sections and chunks.
  - **Rule-based extraction** (`extract.py`): ploidy as the INSDC 4-tuple
    `[level, mechanism, irregular, derivation]`, plus chromosome number.
  - **Vector search ensemble** (`search.py`): BM25 + PubMedBERT embeddings.
  - **Optional local LLM layer** (`gemma.py`): a grounded Gemma-3 vote that also
    contributes sex and strain/cultivar.
- **Grounded, abstaining design:** every value carries an evidence quote, method
  and confidence; the pipeline returns `null` rather than guessing when the text
  has no evidence.
- **Robustness and accuracy fixes:** restored a crashing retrieval path, added
  stale-GCA-version auto-resolution, a host-context relevance penalty, full-text
  aware retrieval, a binomial/genus search fallback, and Unicode/markup parse
  fixes.
- **Nextflow orchestration** (`pipelines/nextflow/`): one process per accession,
  parallelism, automatic retries, and result merging, mirroring the
  `ensembl-genomio` layout.
- **Packaging for upstream:** restructured the code into the
  `ensembl.io.genomio.literature` namespace package with a `literature_metadata`
  console entry point, ready to drop into `ensembl-genomio`.

## Current state

- **Curated benchmark:** 11 / 11 assemblies pass with the full pipeline
  (10 / 11 with rule + vector only; the last needs the LLM layer to resolve
  diploid-ancestor confounding).
- **Scale test:** ran over **1,059 arbitrary plant assemblies** from NCBI with
  **zero unhandled crashes**, species extracted for 98%, and a **paper-grounded**
  ploidy recovered for 67% (never guessed from a reference). Ploidy distribution
  was biologically sensible (di-/tri-/tetra-/penta-/hexa-/octo-/decaploid).
- **Packaged and installable:** `pip install -e .` exposes the
  `literature_metadata` command; the module imports and runs from the
  `ensembl-genomio` tree.

## What is left to do

- Open and iterate the upstream pull request through review.
- Add a curated gold-standard evaluation set to report precision/recall (the
  scale test measures robustness/coverage, not accuracy).
- Add unit tests under the `ensembl-genomio` `pytest` conventions.
- Recover the remaining "no paper found" cases where publications genuinely do
  not yet exist (out of scope of retrieval).

## Where the code is

- **This repository** (single-purpose GSoC repo): the pipeline, benchmark
  (`test_pipeline.py`), robustness sweep (`robustness_test.py`), and the Nextflow
  workflow.
- **Upstream contribution:** the `ensembl.io.genomio.literature` package, added
  to `ensembl-genomio` via pull request:
  **PR: <ADD_PR_URL_HERE>** (branch `literature-metadata-extraction`).
  <!-- Fill in the PR URL after opening it; note the last GSoC commit hash here. -->
- Key commits are listed in this repository's history and in the PR's change log.

## Challenges and things I learned

- **Polyploidy confounding:** in a polyploid genome paper, the diploid *ancestor*
  species are mentioned more often than the target, so naive keyword counting
  fails. This motivated the grounded + ensemble + abstaining design.
- **Retrieval is the hard part.** Two illustrative cases:
  - *Cotton:* the relevance scorer initially ranked a "Bacillus isolated from
    cotton" paper first, because it names the host. Fixed with a host-context
    penalty.
  - *Agapanthus:* the correct ploidy (2n = 4x = 30) appears only in the full text
    of an open-access paper about a different genus. Fixed with full-text-aware
    retrieval plus a confirmation pass.
- **Namespace packaging:** integrating into `ensembl.io.genomio` required PEP 420
  namespace packages and relative imports so the subpackage drops cleanly into
  the existing repository.
- **Orchestration pays off:** wrapping the pipeline in Nextflow gave a roughly
  3x speed-up over the sequential loop and scales by config, not code.
