# Assembly Metadata Extractor

Automatically extracts genomic metadata from genome-assembly papers, given an
NCBI assembly accession. For each accession the pipeline finds the linked
publication, reads its text, and extracts:

- **species** (from NCBI, text fallback)
- **ploidy** (diploid / triploid / tetraploid / hexaploid / ...)
- **chromosome number** (from NCBI, text fallback)
- **cultivar / strain / ecotype**
- **sex**

Ploidy, cultivar and sex are inferred by an **ensemble** of two methods — a
rule-based extractor and a hybrid (BM25 + dense embedding) vector search — so
each field carries a confidence and a method label for review.

## How it works

```
accession
   │
   ▼
fetch.py     NCBI Datasets + Entrez elink + Europe PMC → assembly metadata + linked paper(s)
   │
   ▼
parse.py     full-text XML / abstract → sections + chunks
   │
   ├──────────────► extract.py   rule-based extraction (regex, section weights, title)
   │                                   │
   └──────────────► search.py    vector search (PubMedBERT) + ensemble
                                       │
                                       ▼
                                 final metadata (value + confidence + method)
```

### Ploidy logic

A naive keyword count fails on polyploid genomes, because their **diploid
ancestors** are mentioned far more often than the organism's own ploidy. The
extractor therefore:

1. reads the **paper title** (most authoritative, e.g. *"...segmental
   allotetraploid peanut..."*),
2. treats `2n = Nx = M` formulas and compound terms (`allo-/auto-ploid`) as the
   strongest evidence,
3. **excludes** diploid/polyploid mentions that sit in an ancestor / progenitor
   / donor / "other diploid" context, and
4. falls back to a small curated `KNOWN_PLOIDY` reference (clearly labelled
   `method="reference_taxon"`) only when the text is silent.

## Project structure

```
.
├── src/assembly_metadata/
│   ├── fetch.py        # data retrieval (NCBI / Europe PMC / Semantic Scholar)
│   ├── parse.py        # XML / abstract → sections + chunks
│   ├── extract.py      # rule-based extraction
│   ├── search.py       # vector search + ensemble
│   └── pipeline.py     # orchestration + CLI
├── test_pipeline.py    # end-to-end tests on known accessions
├── requirements.txt
├── .gitignore
└── README.md
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

On CPU the embedding model runs fine; a GPU just makes vector search faster.

## Usage

Run the end-to-end tests:

```bash
python test_pipeline.py
```

Single accession:

```bash
cd src/assembly_metadata
python pipeline.py --accession GCA_003086295.3 --output result.json
```

Batch mode (one accession per line in a text file):

```bash
cd src/assembly_metadata
python pipeline.py --batch accessions.txt --output results.json
```

## Output

Each inferred field reports a `value`, `confidence`, and `method`:

| method        | meaning                              | reliability          |
|---------------|--------------------------------------|----------------------|
| `consensus`   | rule-based and vector agree          | highest              |
| `rule_only`   | rule-based only                      | needs review         |
| `vector_only` | vector search only                   | needs review         |
| `conflict`    | methods disagree                     | manual check needed  |

`reference_taxon` ploidy comes from the curated reference, not the paper, and
should be reviewed.

## Notes & limitations

- Ploidy is extracted from the paper only when stated; otherwise the curated
  `KNOWN_PLOIDY` reference is used (or `unknown` if the taxon is not listed).
- `fetch.py` calls public NCBI / Europe PMC APIs live. Running very frequently
  without an NCBI API key can hit rate limits; add a key + email and a short
  delay between calls for heavy use.
- No credentials are stored in the code. Keep any API keys in a local `.env`
  (git-ignored), never hard-coded.
