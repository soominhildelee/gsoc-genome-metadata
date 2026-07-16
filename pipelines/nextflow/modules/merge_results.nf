/*
 * MERGE_RESULTS — collect every per-accession JSON into one results.json array.
 * Uses only the Python standard library, so it needs no conda env.
 */

process MERGE_RESULTS {
    publishDir "${params.outdir}", mode: 'copy'

    input:
    path json_files

    output:
    path 'results.json'

    script:
    """
    python3 -c "import glob, json; files=[f for f in sorted(glob.glob('*.json')) if f!='results.json']; merged=[json.load(open(f)) for f in files]; json.dump(merged, open('results.json','w'), indent=2, ensure_ascii=False); print('merged', len(merged), 'result(s)')"
    """
}
