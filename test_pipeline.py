import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src", "assembly_metadata"))

from pipeline import run_pipeline


TEST_CASES = [
    {
        "accession":          "GCA_000001735.2",
        "description":        "Arabidopsis thaliana — well-known diploid model plant",
        "expected_level":     2,
        "expected_mechanism": {None},
        "expected_species":   "Arabidopsis thaliana",
    },
    {
        "accession":          "GCA_003086295.3",
        "description":        "Arachis hypogaea (peanut) — segmental allotetraploid",
        "expected_level":     4,
        "expected_mechanism": {"segmental_allopolyploid", "allopolyploid", None},
        "expected_species":   "Arachis hypogaea",
    },
    {
        "accession":          "GCA_002220415.3",
        "description":        "Triticum aestivum (wheat) — hexaploid (amphidiploid)",
        "expected_level":     6,
        "expected_mechanism": {"allopolyploid", "amphidiploid", None},
        "expected_species":   "Triticum aestivum",
    },
    {
        "accession":          "GCA_905183035.1",
        "description":        "Brassica napus (oilseed rape) — tetraploid amphidiploid",
        "expected_level":     4,
        "expected_mechanism": {"amphidiploid", "allopolyploid", None},
        "expected_species":   "Brassica napus",
    },
    {
        "accession":          "GCA_007990345.1",
        "description":        "Gossypium hirsutum (upland cotton) — allotetraploid",
        "expected_level":     4,
        "expected_mechanism": {"allopolyploid", "amphidiploid", None},
        "expected_species":   "Gossypium hirsutum",
    },
    {
        "accession":          "GCA_050043575.1",
        "description":        "Fragaria x ananassa (strawberry) — octoploid",
        "expected_level":     8,
        "expected_mechanism": {"allopolyploid", None},
        "expected_species":   "Fragaria",
    },
    {
        "accession":          "GCA_002210045.1",
        "description":        "Nicotiana tabacum (tobacco) — allotetraploid (Nitab4.5)",
        "expected_level":     4,
        "expected_mechanism": {"allopolyploid", "amphidiploid", None},
        "expected_species":   "Nicotiana tabacum",
    },
    {
        "accession":          "GCA_951802365.1",
        "description":        "Avena sativa (oat) — allohexaploid (cv. Morgan)",
        "expected_level":     6,
        "expected_mechanism": {"allopolyploid", None},
        "expected_species":   "Avena sativa",
    },
    # ── New test cases ─────────────────────────────────────────────────────
    {
        "accession":          "GCA_038442765.1",
        "description":        "Panicum miliaceum (broomcorn millet) — allotetraploid, text says tetraploid, subgenomes A+B in chromosome names",
        "expected_level":     4,
        "expected_mechanism": {"allopolyploid", None},
        "expected_species":   "Panicum miliaceum",
    },
    {
        "accession":          "GCA_902651665.1",
        "description":        "Triticum aestivum (TRITEX paper) — hexaploid; same paper also has T. turgidum tetraploid (disambiguation test)",
        "expected_level":     6,
        "expected_mechanism": {"allopolyploid", "amphidiploid", None},
        "expected_species":   "Triticum aestivum",
    },
    {
        "accession":          "GCA_009802035.1",
        "description":        "Triticum turgidum ssp. dicoccoides (TRITEX paper) — tetraploid; same paper as T. aestivum above (disambiguation test)",
        "expected_level":     4,
        "expected_mechanism": {"allopolyploid", "amphidiploid", None},
        "expected_species":   "Triticum",
    },
    {
        "accession":          "GCA_030015575.1",
        "description":        "Agapanthus africanus (African lily) — tetraploid, indicated as 2n = 4x = 30 in paper",
        "expected_level":     4,
        "expected_mechanism": {"autopolyploid", "allopolyploid", None},
        "expected_species":   "Agapanthus",
    },
]


def check_result(result: dict, expected: dict) -> dict:
    checks = {}

    ploidy     = result.get("ploidy", {})
    got_level  = ploidy.get("level")
    got_mech   = ploidy.get("mechanism")
    checks["ploidy_level"] = {
        "expected": expected["expected_level"],
        "got":      got_level,
        "ok":       got_level == expected["expected_level"],
    }
    # mechanism is informational (not a hard pass/fail) but flagged if unexpected
    checks["ploidy_mechanism"] = {
        "expected": sorted(str(m) for m in expected["expected_mechanism"]),
        "got":      got_mech,
        "ok":       got_mech in expected["expected_mechanism"],
    }

    got_species = result.get("species", {}).get("value", "unknown")
    exp_species = expected["expected_species"]
    checks["species"] = {
        "expected": exp_species,
        "got":      got_species,
        "ok":       exp_species.lower() in got_species.lower(),
    }

    got_accession = result.get("assembly_accession", "")
    checks["accession"] = {
        "expected": expected["accession"],
        "got":      got_accession,
        "ok":       got_accession == expected["accession"],
    }

    checks["paper_found"] = {
        "expected": "> 0 papers",
        "got":      result.get("papers_found", 0),
        "ok":       result.get("papers_found", 0) > 0,
    }

    # Pass criterion: level + species + accession + paper_found.
    # (mechanism is reported but does not fail the test by itself.)
    hard = ["ploidy_level", "species", "accession", "paper_found"]
    passed = all(checks[k]["ok"] for k in hard)
    return {"passed": passed, "checks": checks}


def run_tests(test_cases: list[dict], max_results: int = 10) -> None:
    print("=" * 60)
    print("Pipeline Test Run")
    print(f"Testing {len(test_cases)} accessions")
    print("=" * 60)

    all_results = []

    for i, case in enumerate(test_cases, 1):
        accession   = case["accession"]
        description = case["description"]

        print(f"\n[Test {i}/{len(test_cases)}] {accession}")
        print(f"  {description}")
        print("-" * 60)

        try:
            result    = run_pipeline(accession, max_results=max_results)
            evaluated = check_result(result, case)

            all_results.append({
                "accession":  accession,
                "status":     "success",
                "passed":     evaluated["passed"],
                "checks":     evaluated["checks"],
                "raw_result": result,
            })

            print(f"\n  Test Results:")
            for field, check in evaluated["checks"].items():
                icon = "✅" if check["ok"] else "❌"
                print(f"  {icon} {field:<20} expected={check['expected']}  got={check['got']}")

            if evaluated["passed"]:
                print(f"\n  ✅ PASSED")
            else:
                print(f"\n  ❌ FAILED — check fields above")

        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            all_results.append({
                "accession": accession,
                "status":    "error",
                "error":     str(e),
                "passed":    False,
            })

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed  = sum(1 for r in all_results if r.get("passed"))
    errored = sum(1 for r in all_results if r.get("status") == "error")
    total   = len(all_results)

    for r in all_results:
        if r.get("status") == "error":
            icon = "💥"
        elif r.get("passed"):
            icon = "✅"
        else:
            icon = "❌"
        print(f"  {icon} {r['accession']}")

    print(f"\n  Passed:  {passed}/{total}")
    print(f"  Failed:  {total - passed - errored}/{total}")
    print(f"  Errored: {errored}/{total}")

    output_path = "test_results.json"
    with open(output_path, "w") as f:
        safe_results = []
        for r in all_results:
            try:
                json.dumps(r)
                safe_results.append(r)
            except TypeError:
                safe_results.append({
                    "accession": r["accession"],
                    "status":    r.get("status"),
                    "passed":    r.get("passed"),
                    "note":      "raw_result not serialisable",
                })
        json.dump(safe_results, f, indent=2, ensure_ascii=False)

    print(f"\n  Full results saved to {output_path}")
    print("=" * 60)

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    run_tests(TEST_CASES, max_results=3)