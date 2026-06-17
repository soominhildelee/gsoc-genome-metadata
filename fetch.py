# src/assembly_metadata/fetch.py
import time
import requests


# ============================================================
# STEP 1 — Get assembly info from NCBI
# ============================================================

def fetch_assembly_metadata(accession: str) -> dict:
    url = (
        f"https://api.ncbi.nlm.nih.gov/datasets/v2/genome/accession"
        f"/{accession}/dataset_report"
    )
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"  [NCBI] Failed to fetch {accession}: {e}")
        return {
            "assembly_accession": accession,
            "chromosome_number":  None,
            "chromosome_source":  None,
        }

    reports = data.get("reports", [])
    if not reports:
        print(f"  [NCBI] No report found for {accession}")
        return {
            "assembly_accession": accession,
            "chromosome_number":  None,
            "chromosome_source":  None,
        }

    report         = reports[0]
    assembly_info  = report.get("assembly_info", {})
    organism       = report.get("organism", {})
    assembly_stats = report.get("assembly_stats", {})

    chromosome_number = (
        assembly_stats.get("total_number_of_chromosomes")
        or assembly_info.get("chromosome_count")
    )

    raw_pmid = (
        assembly_info.get("linked_pmid")
        or assembly_info.get("biosample", {}).get("linked_pmid")
    )
    linked_pmids = (
        raw_pmid if isinstance(raw_pmid, list)
        else [raw_pmid] if raw_pmid
        else []
    )

    return {
        "assembly_accession": accession,
        "assembly_name":      assembly_info.get("assembly_name"),
        "taxon_id":           str(organism.get("tax_id", "")),
        "scientific_name":    organism.get("organism_name"),
        "linked_pmids":       linked_pmids,
        "chromosome_number":  chromosome_number,
        "chromosome_source":  "ncbi" if chromosome_number else None,
    }


# ============================================================
# STEP 2 — Find linked papers
#           Strategy:
#           1st: directly linked PMIDs from NCBI assembly record
#           2nd: NCBI Entrez elink (curated assembly -> pubmed links)
#                — this is the most reliable source
#           3rd: Europe PMC text search by scientific name
#           4th: NCBI Entrez PMC search
#           5th: Semantic Scholar
# ============================================================

def search_europe_pmc(query: str, max_results: int = 5, retries: int = 3) -> list:
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {
        "query":      query,
        "format":     "json",
        "pageSize":   max_results,
        "resultType": "core",
    }
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json().get("resultList", {}).get("result", [])
        except requests.RequestException as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"  [EuropePMC] Attempt {attempt+1} failed, retrying in {wait}s ...")
                time.sleep(wait)
            else:
                print(f"  [EuropePMC] All {retries} attempts failed: {e}")
                return []
    return []


def search_by_pmid(pmid: str) -> dict | None:
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {
        "query":      f"EXT_ID:{pmid} AND SRC:MED",
        "format":     "json",
        "resultType": "core",
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        results = response.json().get("resultList", {}).get("result", [])
        return results[0] if results else None
    except requests.RequestException:
        return None


def search_europe_pmc_by_name(
    taxon_id:        str,
    scientific_name: str,
    max_results:     int = 5,
) -> list:
    genus = scientific_name.split()[0] if scientific_name else ""

    queries = [
        # 1st priority: exact species name + genome assembly
        f'"{scientific_name}" AND "genome assembly"',
        # 2nd priority: exact species name + chromosome + ploidy
        f'"{scientific_name}" AND chromosome AND ploidy',
        # 3rd priority: exact species name + genome sequence
        f'"{scientific_name}" AND "genome sequence"',
        # 4th priority: taxon ID fallback (last resort)
        f"TAXONOMY:{taxon_id} AND genome assembly",
    ]

    for query in queries:
        results = search_europe_pmc(query, max_results=max_results * 3)
        if not results:
            continue

        relevant = [
            r for r in results
            if genus.lower() in (
                r.get("title", "") + r.get("abstractText", "")
            ).lower()
        ]

        if relevant:
            print(f"  [EuropePMC] Query: {query}")
            print(f"              Found {len(results)} results, "
                  f"{len(relevant)} relevant to {scientific_name}")
            return relevant[:max_results]

    print(f"  [EuropePMC] No relevant papers found for {scientific_name}")
    return []


def has_usable_fulltext(papers: list[dict]) -> bool:
    # Returns True only if at least one paper has a PMCID
    # and is NOT an organelle genome paper
    ORGANELLE_KEYWORDS = [
        "chloroplast", "mitochondria", "plastid",
        "organelle", "cpdna", "mtdna", "plastome",
    ]
    for p in papers:
        pmcid = p.get("pmcid") or p.get("pmCid") or ""
        if not pmcid:
            continue
        title    = p.get("title", "").lower()
        abstract = (p.get("abstractText", "") or "").lower()
        is_organelle = any(
            kw in title or kw in abstract
            for kw in ORGANELLE_KEYWORDS
        )
        if not is_organelle:
            return True
    return False


def fetch_linked_pubmed_for_assembly(accession: str, max_results: int = 5) -> list:
    # Step 1: convert GCA accession to NCBI assembly ID
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    try:
        response     = requests.get(search_url, params={
            "db": "assembly", "term": accession, "retmode": "json"
        }, timeout=30)
        response.raise_for_status()
        assembly_ids = response.json().get("esearchresult", {}).get("idlist", [])
    except requests.RequestException as e:
        print(f"  [NCBI Entrez elink] Assembly search failed: {e}")
        return []

    if not assembly_ids:
        print(f"  [NCBI Entrez elink] No assembly ID found for {accession}")
        return []

    assembly_id = assembly_ids[0]
    print(f"  [NCBI Entrez elink] Assembly ID: {assembly_id}")

    # Step 2: find linked pubmed articles via elink
    elink_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
    try:
        response   = requests.get(elink_url, params={
            "dbfrom": "assembly", "db": "pubmed",
            "id": assembly_id, "retmode": "json"
        }, timeout=30)
        response.raise_for_status()
        data       = response.json()
        linksets   = data.get("linksets", [{}])[0]
        pmids      = []
        for ldb in linksets.get("linksetdbs", []):
            if ldb.get("dbto") == "pubmed":
                pmids = ldb.get("links", [])
                break
    except requests.RequestException as e:
        print(f"  [NCBI Entrez elink] elink failed: {e}")
        return []

    if not pmids:
        print(f"  [NCBI Entrez elink] No linked publications found")
        return []

    print(f"  [NCBI Entrez elink] Found {len(pmids)} linked PMID(s): {pmids[:5]}")

    # Step 3: fetch paper details from Europe PMC using PMIDs
    papers = []
    for pmid in pmids[:max_results]:
        paper = search_by_pmid(str(pmid))
        if paper:
            papers.append(paper)
            print(f"  [NCBI Entrez elink] {pmid}: {paper.get('title', '')[:60]}...")

    return papers


def search_ncbi_entrez(scientific_name: str, max_results: int = 5) -> list:
    print(f"  [NCBI Entrez] Searching PubMed Central for {scientific_name} ...")

    # Step 1: search for PMC IDs
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    try:
        response = requests.get(search_url, params={
            "db":      "pmc",
            "term":    f'"{scientific_name}" AND "genome assembly" AND "open access"[filter]',
            "retmax":  max_results * 2,
            "retmode": "json",
        }, timeout=30)
        response.raise_for_status()
        pmc_ids = response.json().get("esearchresult", {}).get("idlist", [])
    except requests.RequestException as e:
        print(f"  [NCBI Entrez] Search failed: {e}")
        return []

    if not pmc_ids:
        print(f"  [NCBI Entrez] No PMC IDs found")
        return []

    # Step 2: fetch summaries
    summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    try:
        response  = requests.get(summary_url, params={
            "db": "pmc", "id": ",".join(pmc_ids[:max_results]), "retmode": "json"
        }, timeout=30)
        response.raise_for_status()
        summaries = response.json().get("result", {})
    except requests.RequestException as e:
        print(f"  [NCBI Entrez] Summary fetch failed: {e}")
        return []

    # Step 3: convert to Europe PMC format
    papers = []
    for pmc_id in pmc_ids[:max_results]:
        summary = summaries.get(pmc_id, {})
        if not summary:
            continue
        pmcid = f"PMC{pmc_id}"
        papers.append({
            "title":        summary.get("title", ""),
            "pmcid":        pmcid,
            "pmid":         summary.get("pmid", ""),
            "doi":          summary.get("doi", ""),
            "abstractText": "",
            "source":       "ncbi_entrez",
        })
        print(f"  [NCBI Entrez] {pmcid}: {summary.get('title', '')[:60]}...")

    return papers


def search_semantic_scholar(scientific_name: str, max_results: int = 5) -> list:
    print(f"  [Semantic Scholar] Searching for {scientific_name} ...")

    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    try:
        response   = requests.get(url, params={
            "query":  f"{scientific_name} genome assembly ploidy",
            "fields": "title,abstract,externalIds,openAccessPdf",
            "limit":  max_results * 2,
        }, timeout=30)
        response.raise_for_status()
        papers_raw = response.json().get("data", [])
    except requests.RequestException as e:
        print(f"  [Semantic Scholar] Search failed: {e}")
        return []

    genus  = scientific_name.split()[0].lower()
    papers = []
    for p in papers_raw:
        title    = p.get("title", "")
        abstract = p.get("abstract", "") or ""
        if genus not in (title + abstract).lower():
            continue
        ext_ids = p.get("externalIds", {})
        papers.append({
            "title":        title,
            "pmcid":        f"PMC{ext_ids['PubMedCentral']}" if "PubMedCentral" in ext_ids else "",
            "pmid":         str(ext_ids.get("PubMed", "")),
            "doi":          ext_ids.get("DOI", ""),
            "abstractText": abstract,
            "source":       "semantic_scholar",
        })
        print(f"  [Semantic Scholar] {title[:60]}...")
        if len(papers) >= max_results:
            break

    print(f"  [Semantic Scholar] Found {len(papers)} relevant paper(s)")
    return papers


# ============================================================
# STEP 3 — Validate DOI / PMID / PMCID
# ============================================================

def validate_paper_ids(paper: dict) -> dict:
    pmcid = paper.get("pmcid")
    pmid  = paper.get("pmid")
    doi   = paper.get("doi")

    pmcid_valid = False
    if pmcid:
        try:
            url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
            r   = requests.head(url, timeout=10)
            pmcid_valid = r.status_code == 200
        except requests.RequestException:
            pmcid_valid = False

    pmid_valid = False
    if pmid:
        try:
            result     = search_by_pmid(pmid)
            pmid_valid = result is not None
        except requests.RequestException:
            pmid_valid = False

    return {
        "pmcid":          pmcid,
        "pmid":           pmid,
        "doi":            doi,
        "pmcid_valid":    pmcid_valid,
        "pmid_valid":     pmid_valid,
        "doi_present":    bool(doi),
        "has_fulltext":   pmcid_valid,
        "best_available": (
            "fulltext" if pmcid_valid else
            "abstract" if pmid_valid else
            "none"
        ),
    }


# ============================================================
# STEP 4 — Download full text or abstract
# ============================================================

def get_fulltext_by_pmcid(pmcid: str) -> str | None:
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            return response.text
    except requests.RequestException:
        pass
    return None


def get_abstract_by_pmid(pmid: str) -> str | None:
    paper = search_by_pmid(pmid)
    if paper:
        return paper.get("abstractText")
    return None


def get_best_available_text(paper: dict) -> dict:
    pmcid = paper.get("pmcid")
    pmid  = paper.get("pmid")

    if pmcid:
        fulltext = get_fulltext_by_pmcid(pmcid)
        if fulltext:
            return {"source": "fulltext", "pmcid": pmcid, "text": fulltext}

    if pmid:
        abstract = get_abstract_by_pmid(pmid)
        if abstract:
            return {"source": "abstract", "pmid": pmid, "text": abstract}

    abstract = paper.get("abstractText")
    if abstract:
        return {"source": "search_result", "text": abstract}

    return {"source": "none", "text": None}


# ============================================================
# MAIN FUNCTION — run all steps above in order
#           input:  assembly accession (e.g. 'GCA_003086295.3')
#           output: assembly metadata + linked papers with full text
# ============================================================

def fetch_papers_for_assembly(accession: str, max_results: int = 5) -> dict:
    print(f"\n[1/4] Fetching assembly metadata for {accession} ...")
    assembly        = fetch_assembly_metadata(accession)
    scientific_name = assembly.get("scientific_name", "")
    taxon_id        = assembly.get("taxon_id", "")

    print(f"      taxon_id          = {taxon_id}")
    print(f"      scientific_name   = {scientific_name}")
    print(f"      assembly_name     = {assembly.get('assembly_name')}")
    print(f"      chromosome_number = {assembly.get('chromosome_number')} "
          f"(source: {assembly.get('chromosome_source') or 'not found -> will try text extraction'})")

    papers_found = []

    # Priority 1: directly linked PMIDs from NCBI assembly record
    linked_pmids = assembly.get("linked_pmids", [])
    if linked_pmids:
        print(f"\n[2/4] Fetching {len(linked_pmids)} directly linked paper(s) ...")
        for pmid in linked_pmids:
            paper = search_by_pmid(pmid)
            if paper:
                papers_found.append(paper)
                print(f"      Found: {paper.get('title', '')[:70]}...")

    # Priority 2: NCBI Entrez elink — always try this
    # elink uses curated assembly -> pubmed links from NCBI,
    # which is more reliable than free-text search
    if not papers_found:
        print(f"\n[2/4] Trying NCBI Entrez elink for {accession} ...")
        elink_papers = fetch_linked_pubmed_for_assembly(accession, max_results=max_results)
        if elink_papers:
            papers_found = elink_papers

    # Priority 3: Europe PMC text search — fallback when elink finds nothing
    if not papers_found:
        print(f"\n[2/4] Trying Europe PMC search for {scientific_name} ...")
        papers_found = search_europe_pmc_by_name(
            taxon_id, scientific_name, max_results=max_results
        )

    # Priority 4: NCBI Entrez PMC search
    if not papers_found:
        print(f"\n[2/4] Trying NCBI Entrez PMC search for {scientific_name} ...")
        papers_found = search_ncbi_entrez(scientific_name, max_results=max_results)

    # Priority 5: Semantic Scholar — final fallback
    if not papers_found:
        print(f"\n[2/4] Trying Semantic Scholar for {scientific_name} ...")
        papers_found = search_semantic_scholar(scientific_name, max_results=max_results)

    if not papers_found:
        print(f"\n[2/4] All search sources exhausted. No papers found.")

    print(f"\n[3/4] Validating IDs and fetching text ...")
    enriched_papers = []
    for paper in papers_found:
        validation = validate_paper_ids(paper)
        text_data  = get_best_available_text(paper)
        enriched_papers.append({
            "title":      paper.get("title", ""),
            "pmcid":      paper.get("pmcid"),
            "pmid":       paper.get("pmid"),
            "doi":        paper.get("doi"),
            "validation": validation,
            "text_data":  text_data,
        })
        print(f"      [{validation['best_available']:>10}]  "
              f"{paper.get('title', '')[:60]}...")

    print(f"\n[4/4] Done. {len(enriched_papers)} paper(s) ready for extraction.")
    print(f"      chromosome_number will be "
          f"{'used from NCBI' if assembly.get('chromosome_number') else 'extracted from text in extract.py'}")

    return {
        "assembly": assembly,
        "papers":   enriched_papers,
    }