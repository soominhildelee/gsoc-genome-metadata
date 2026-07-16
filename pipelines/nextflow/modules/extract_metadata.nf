/*
 * EXTRACT_METADATA — run the Python pipeline for one assembly accession.
 *
 * Calls the existing CLI (pipeline.py --accession ...) unchanged; the module is
 * a thin wrapper so Nextflow can parallelise, retry transient NCBI/EuropePMC
 * failures, and cache completed accessions across -resume runs.
 */

process EXTRACT_METADATA {
    tag "${accession}"

    // publish each result; mode 'copy' so outputs survive work-dir cleanup
    publishDir "${params.outdir}/per_accession", mode: 'copy'

    // Transient API errors (e.g. NCBI elink 500) are common — let Nextflow retry.
    errorStrategy 'retry'
    maxRetries    2

    input:
    val accession

    output:
    path "${accession}.json", emit: json

    script:
    """
    GEMMA_ENABLED=${params.gemma_enabled} \\
    conda run -n ${params.conda_env} python ${params.pipeline_dir}/pipeline.py \\
        --accession ${accession} \\
        --max_results ${params.max_results} \\
        --output ${accession}.json
    """
}
