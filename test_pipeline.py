import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src", "assembly_metadata"))

from pipeline import run_pipeline


TEST_CASES = [
    {
        "accession":        "GCA_000001735.2",
        "description":      "Arabidopsis thaliana — well-known diploid model plant",
        "expected_ploidy":  "diploid",
        "expected_species": "Arabidopsis thaliana",
    },
    {
        "accession":        "GCA_003086295.3",
        "description":      "Arachis hypogaea (peanut) — allotetraploid",
        "expected_ploidy":  "tetraploid",
        "expected_species": "Arachis hypogaea",
    },
    {
        "accession":        "GCA_002220415.3",  # 변경 — Triticum_4.0, 논문 연결 더 좋음
        "description":      "Triticum aestivum (wheat) — hexaploid",
        "expected_ploidy":  "hexaploid",
        "expected_species": "Triticum aestivum",
    },
]


def check_result(result: dict, expected: dict) -> dict:
    checks = {}

    got_ploidy = result.get("ploidy", {}).get("value", "unknown")
    checks["ploidy"] = {
        "expected": expected["expected_ploidy"],
        "got":      got_ploidy,
        "ok":       got_ploidy == expected["expected_ploidy"],
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

    passed = all(c["ok"] for c in checks.values())
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