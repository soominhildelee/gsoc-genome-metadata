# Assembly Metadata Extractor

> **Google Summer of Code 2026 (EMBL-EBI / Ensembl).** For the GSoC final report
> (goals, what was done, current state, what is left, upstream contribution, and
> challenges) see **[WORK_PRODUCT.md](WORK_PRODUCT.md)**.

Automatically extracts genomic metadata from genome-assembly papers, given an
NCBI assembly accession. For each accession the pipeline finds the linked
publication, reads its text (including supplementary files), and extracts:

- **species** (from NCBI, text fallback)
- **ploidy** (diploid / triploid / tetraploid / hexaploid / ...) as an INSDC
  4-tuple `[level, mechanism, irregular, derivation]`
- **chromosome number** (from NCBI, text fallback)
- **cultivar / strain / ecotype**
- **sex**

Ploidy, cultivar, sex and strain are inferred by an **ensemble** of three
methods — a rule-based extractor, a hybrid (BM25 + dense embedding) vector
search, and an optional local **Gemma** LLM layer — so each field carries a
confidence and a method label for review.

## How it works

```
accession
   │
   ▼
fetch.py     NCBI Datasets + Entrez elink + BioProject + Europe PMC
   │           → assembly metadata + linked paper(s) + supplementary files
   ▼
parse.py     full-text XML / abstract / supplementary → sections + chunks
   │
   ├──────────────► extract.py   rule-based extraction (regex, section weights, title)
   │                                   │
   ├──────────────► search.py    vector search (PubMedBERT) + ensemble
   │                                   │
   └──────────────► gemma.py     optional local Gemma-3 (grounded LLM extraction)
                                       │
                                       ▼
                     pipeline.py   final metadata (value + confidence + method)
```

All modules live at the repository root; `pipeline.py` orchestrates them and is
the CLI entry point.

### Ploidy logic

A naive keyword count fails on polyploid genomes, because their **diploid
ancestors** are mentioned far more often than the organism's own ploidy. The
extractor therefore:

1. reads the **paper title** (most authoritative, e.g. *"...segmental
   allotetraploid peanut..."*),
2. treats `2n = Nx = M` formulas and compound terms (`allo-/auto-ploid`) as the
   strongest evidence (whitespace, incl. Unicode thin spaces, is normalised so
   `2n = 4x = 30` is read correctly),
3. **excludes** diploid/polyploid mentions that sit in an ancestor / progenitor
   / donor context, and
4. falls back to a curated reference (GoaT / `KNOWN_PLOIDY`, labelled
   `reference_taxon`) only when the text is silent.

The optional **Gemma layer** (`gemma.py`) reads the same evidence passages and
returns a grounded ploidy candidate; `combine_with_gemma` folds it in as a third
vote (agreement raises confidence to `consensus_3way`; disagreement is flagged
for review). It also contributes **sex** and **strain/cultivar** when stated.

### Paper retrieval

Candidates are pooled from several sources and ranked by `score_paper_candidate`:
directly linked PMIDs → NCBI Entrez elink → BioProject reference paper → Europe
PMC name search → Europe PMC **full-text species search** → NCBI Entrez PMC →
Semantic Scholar. Two notable guards:

- **Host-context penalty** — a paper about a microbe *isolated from* / *associated
  with* the target (e.g. *"Draft genome of Bacillus … isolated from cotton"*)
  names the species only as a habitat; it is penalised as hard as an organelle
  paper so it cannot rank as the genome paper.
- **Full-text-aware retrieval** — when no clearly-strong genome paper surfaces,
  the pipeline searches Europe PMC full text for the exact binomial (catching
  multi-species / methods papers that name the species only in the body),
  confirms each hit by fetching its full text, and rewards a confirmed source so
  it can outrank an off-target paper that merely names the species up front.

Stale GCA version suffixes (e.g. `GCA_000001405.15` when `.29` is current) are
auto-resolved to the latest version, so an out-of-date accession list still runs.

## Project structure

```
.
├── fetch.py            # data retrieval (NCBI / BioProject / Europe PMC), supplementary files
├── parse.py            # XML / abstract / supplementary → sections + chunks
├── extract.py          # rule-based extraction
├── search.py           # vector search + ensemble
├── gemma.py            # optional local Gemma-3 LLM layer (grounded)
├── pipeline.py         # orchestration + CLI
├── test_pipeline.py    # end-to-end benchmark on known accessions
├── robustness_test.py  # batch robustness sweep over many accessions
├── accessions.txt      # accession list for the robustness sweep
├── run_gemma_server.sh # optional: shared Gemma server (not required)
├── requirements.txt
└── README.md
```

## Setup

```bash
conda activate ensembl          # or: python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

On CPU the embedding model runs fine; a GPU just makes vector search faster.

## Usage

Single accession:

```bash
python pipeline.py --accession GCA_002220415.3 --output result.json
```

Batch mode (one accession per line in a text file):

```bash
python pipeline.py --batch accessions.txt --output results.json
```

End-to-end benchmark:

```bash
python test_pipeline.py                 # Gemma auto-detected if a model is present
GEMMA_ENABLED=0 python test_pipeline.py # rule + vector only (faster)
```

Robustness sweep over many accessions:

```bash
python robustness_test.py --accessions accessions.txt --limit 5   # dry run
python robustness_test.py --accessions accessions.txt             # full
```

### The Gemma layer

The Gemma LLM layer is **optional** and off unless a model is available. Control
it with `GEMMA_ENABLED` (`1` = force on, `0` = force off; unset = auto-detect a
GGUF file / HF weights / running server). Models are **not** committed to the
repo — place a GGUF at `models/google_gemma-3-4b-it-Q4_K_M.gguf` (or set
`GEMMA_MODEL`) to enable it. The pipeline loads the GGUF in-process; no server is
needed. `run_gemma_server.sh` is only for sharing one model across parallel runs.

## Output

Each inferred field reports a `value`/`level`, `confidence`, and `method`:

| method            | meaning                                   | reliability          |
|-------------------|-------------------------------------------|----------------------|
| `consensus_3way`  | rule, vector and Gemma agree              | highest              |
| `consensus`       | rule-based and vector agree               | high                 |
| `rule_only`       | rule-based only                           | needs review         |
| `vector_only`     | vector search only                        | needs review         |
| `gemma_only`      | Gemma found an explicit statement alone   | needs review         |
| `conflict...`     | methods disagree                          | manual check needed  |
| `reference_taxon` | curated reference (GoaT / KNOWN_PLOIDY)   | not from paper       |

## Notes & limitations

- Ploidy is extracted from the paper when stated; otherwise the curated
  reference is used (or `unknown` if the taxon is not listed).
- Retrieval depends on the correct paper being reachable. Some assemblies report
  their ploidy only in a paywalled or off-target paper (a species named in
  another organism's genome paper); those remain hard cases.
- `fetch.py` calls public NCBI / Europe PMC APIs live. Running very frequently
  without an NCBI API key can hit rate limits; add a key + email and a short
  delay between calls for heavy use.
- No credentials are stored in the code. Keep any API keys in a local `.env`
  (git-ignored), never hard-coded.
