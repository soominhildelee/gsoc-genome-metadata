#!/usr/bin/env nextflow

/*
 * Assembly-metadata extraction — Nextflow orchestration layer.
 *
 * The scientific logic stays in the Python package (pipeline.py and friends);
 * Nextflow only fans an accession list out across processes and gathers the
 * results. This mirrors how ensembl-genomio wraps its Python modules in
 * pipelines/nextflow/ — one process per work item, config-driven parallelism.
 */

nextflow.enable.dsl = 2

include { EXTRACT_METADATA } from './modules/extract_metadata.nf'
include { MERGE_RESULTS    } from './modules/merge_results.nf'

workflow {
    // One accession per line. Reproduces the accessions.txt convention: strip
    // inline "# species (ploidy)" comments, drop blanks, keep the first token.
    accessions = Channel
        .fromPath(params.accessions)
        .splitText()
        .map    { it.split('#')[0].trim() }
        .filter { it }
        .map    { it.split(/\s+/)[0] }

    // One assembly = one process. Nextflow gives us parallelism, retries and
    // -resume for free (replacing the hand-rolled for-loop in run_batch).
    EXTRACT_METADATA(accessions)

    // Gather every per-accession JSON into a single results.json array.
    MERGE_RESULTS(EXTRACT_METADATA.out.json.collect())
}
