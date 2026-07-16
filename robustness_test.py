import argparse
import csv
import json
import os
import sys
import time
import traceback
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src", "assembly_metadata"))

from pipeline import run_pipeline

CACHE_DIR = "robustness_cache"
OUT_JSON  = "robustness_results.json"
OUT_CSV   = "robustness_summary.csv"

# Used only when no --accessions file is given (smoke test).
DEFAULT_ACCESSIONS = [
    "GCA_000001735.2",   # Arabidopsis thaliana — diploid
    "GCA_003086295.3",   # Arachis hypogaea — tetraploid
    "GCA_002220415.3",   # Triticum aestivum — hexaploid
]

CSV_FIELDS = [
    "accession", "status", "failure_mode",
    "species", "taxon_id", "papers_found",
    "retrieval_source", "relevance_score", "text_source",
    "ploidy_level", "ploidy_mechanism", "ploidy_method", "ploidy_confidence",
    "chromosome_number", "chromosome_source",
    "n_cultivars", "sex",
]


def classify(result, error_trace):
    if error_trace:
        first = error_trace.strip().splitlines()[-1]
        etype = first.split(":")[0].strip() if ":" in first else "Exception"
        return f"error:{etype}"
    if not isinstance(result, dict):
        return "error:bad_result"
    if result.get("error"):
        return result["error"]
    if result.get("papers_found", 0) == 0:
        return "no_papers"
    return "success"


def to_row(accession, result, failure_mode):
    ploidy = result.get("ploidy", {}) if isinstance(result, dict) else {}
    paper  = result.get("paper", {}) if isinstance(result, dict) else {}
    chrom  = result.get("chromosome_number", {}) if isinstance(result, dict) else {}
    cult   = result.get("cultivars", {}) if isinstance(result, dict) else {}
    sex    = result.get("sex", {}) if isinstance(result, dict) else {}
    species = result.get("species", {}) if isinstance(result, dict) else {}

    n_cult = 0
    if isinstance(cult, dict):
        n_cult = len(cult.get("confirmed", []) or [])
    elif isinstance(cult, list):
        n_cult = len(cult)

    return {
        "accession":         accession,
        "status":            "success" if failure_mode == "success" else "fail",
        "failure_mode":      failure_mode,
        "species":           species.get("value") if isinstance(species, dict) else None,
        "taxon_id":          result.get("taxon_id") if isinstance(result, dict) else None,
        "papers_found":      result.get("papers_found") if isinstance(result, dict) else None,
        "retrieval_source":  paper.get("retrieval_source"),
        "relevance_score":   paper.get("relevance_score"),
        "text_source":       paper.get("text_source"),
        "ploidy_level":      ploidy.get("level"),
        "ploidy_mechanism":  ploidy.get("mechanism"),
        "ploidy_method":     ploidy.get("method"),
        "ploidy_confidence": ploidy.get("confidence"),
        "chromosome_number": chrom.get("value") if isinstance(chrom, dict) else None,
        "chromosome_source": chrom.get("source") if isinstance(chrom, dict) else None,
        "n_cultivars":       n_cult,
        "sex":               sex.get("value") if isinstance(sex, dict) else None,
    }


def run(accessions, max_results, sleep, use_cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    total = len(accessions)
    rows, raw = [], []

    for i, acc in enumerate(accessions, 1):
        print(f"\n{'#' * 60}\n# {i}/{total}  {acc}\n{'#' * 60}")
        cache_file = os.path.join(CACHE_DIR, f"{acc}.json")
        error_trace = None

        if use_cache and os.path.exists(cache_file):
            print(f"  (cached)")
            with open(cache_file) as f:
                result = json.load(f)
        else:
            try:
                result = run_pipeline(acc, max_results=max_results)
                with open(cache_file, "w") as f:
                    json.dump(result, f, ensure_ascii=False)
            except Exception:
                error_trace = traceback.format_exc()
                result = {"assembly_accession": acc}
                print(f"  ERROR:\n{error_trace}")
            if sleep:
                time.sleep(sleep)

        failure_mode = classify(result, error_trace)
        rows.append(to_row(acc, result, failure_mode))
        raw.append({"accession": acc, "failure_mode": failure_mode,
                    "error": error_trace, "result": result})

    # ---- write outputs ----
    with open(OUT_JSON, "w") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    # ---- report ----
    print("\n" + "=" * 60)
    print("Robustness Summary")
    print("=" * 60)

    modes = Counter(r["failure_mode"] for r in rows)
    n_success = modes.get("success", 0)
    print(f"  Total: {total}   Success: {n_success}   Fail: {total - n_success}")
    print("\n  Failure modes:")
    for mode, n in modes.most_common():
        print(f"    {n:>4}  {mode}")

    succ = [r for r in rows if r["failure_mode"] == "success"]
    if succ:
        def pct(cond):
            return f"{100 * sum(1 for r in succ if cond(r)) / len(succ):.0f}%"
        print("\n  Field coverage (of successful runs):")
        print(f"    ploidy level present : {pct(lambda r: r['ploidy_level'] is not None)}")
        print(f"    ploidy from text     : {pct(lambda r: r['ploidy_method'] not in (None, 'reference_taxon', 'no_text_evidence', 'both_unknown'))}")
        print(f"    mechanism present    : {pct(lambda r: r['ploidy_mechanism'] is not None)}")
        print(f"    >=1 cultivar         : {pct(lambda r: (r['n_cultivars'] or 0) > 0)}")
        print(f"    sex known            : {pct(lambda r: r['sex'] not in (None, 'unknown'))}")
        print(f"    fulltext used        : {pct(lambda r: r['text_source'] == 'fulltext')}")

    print(f"\n  Per-accession CSV : {OUT_CSV}")
    print(f"  Full results JSON : {OUT_JSON}")
    print(f"  Fetch cache       : {CACHE_DIR}/  (delete to force re-fetch)")
    print("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Batch robustness test over many accessions.")
    ap.add_argument("--accessions", type=str, help="Text file, one accession per line")
    ap.add_argument("--max_results", type=int, default=5)
    ap.add_argument("--sleep", type=float, default=1.0, help="Seconds between accessions (NCBI rate limit)")
    ap.add_argument("--limit", type=int, default=None, help="Only run the first N accessions")
    ap.add_argument("--no-cache", action="store_true", help="Ignore cache and re-fetch everything")
    args = ap.parse_args()

    if args.accessions:
        accessions = []
        with open(args.accessions) as f:
            for ln in f:
                ln = ln.split("#")[0].strip()   # allow inline "# species (ploidy)" comments
                if ln:
                    accessions.append(ln.split()[0])
    else:
        print("No --accessions file given; running the 3 default smoke-test accessions.")
        accessions = DEFAULT_ACCESSIONS

    if args.limit:
        accessions = accessions[:args.limit]

    run(accessions, max_results=args.max_results, sleep=args.sleep, use_cache=not args.no_cache)