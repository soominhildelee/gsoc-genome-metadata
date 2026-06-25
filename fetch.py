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
        "common_name":        organism.get("common_name"),   # NEW — mentor: search by common name too
        "linked_pmids":       linked_pmids,
        "chromosome_number":  chromosome_number,
        "chromosome_source":  "ncbi" if chromosome_number else None,
    }


# ============================================================
# STEP 2 — Find linked papers
#           Strategy (Mode B — escalate on weak result):
#           1st: directly linked PMIDs from NCBI assembly record
#           2nd: NCBI Entrez elink (curated assembly -> pubmed links)
#           -- if no STRONG genome-paper candidate from the above, escalate:
#           3rd: Europe PMC search by scientific_name AND common_name
#           4th: NCBI Entrez PMC search
#           5th: Semantic Scholar
#           Candidates from all sources are POOLED, de-duplicated, and
#           ranked best-first by score_paper_candidate().
# ============================================================

ORGANELLE_KEYWORDS = [
    "chloroplast", "mitochondria", "plastid",
    "organelle", "cpdna", "mtdna", "plastome",
]
GENOME_KEYWORDS = [
    "genome assembly", "genome sequence", "reference genome",
    "chromosome-level", "chromosome-scale", "de novo assembly",
    "whole genome", "whole-genome", "genome of",
]
PLOIDY_HINTS = [
    "diploid", "triploid", "tetraploid", "pentaploid", "hexaploid",
    "octoploid", "polyploid", "allopolyploid", "autopolyploid",
    "amphidiploid", "ploidy", "2n=", "2n =",
]

# A candidate scoring >= STRONG_SCORE is treated as a confident genome paper,
# so Mode B will NOT escalate to extra name-based searches.
STRONG_SCORE = 6.0


def score_paper_candidate(paper: dict, identifiers: dict) -> float:
    """Relevance score for "is this the genome/ploidy paper for THIS species?".

    Used both to rank pooled candidates and to decide Mode-B escalation.
    """
    title    = (paper.get("title") or "").lower()
    abstract = (paper.get("abstractText") or "").lower()
    text     = f"{title} {abstract}"

    sci    = (identifiers.get("scientific_name") or "").lower()
    common = (identifiers.get("common_name") or "").lower()
    genus  = sci.split()[0] if sci else ""

    score = 0.0

    # name match (scientific > common > genus)
    if sci and sci in title:
        score += 3.0
    elif sci and sci in text:
        score += 1.5
    if common and len(common) >= 4 and common in title:
        score += 1.5
    if genus and genus in title:
        score += 1.0

    # genome-paper signal (word-order tolerant: "assembly of the ... genome")
    if any(k in title for k in GENOME_KEYWORDS):
        score += 3.0
    elif "genome" in title and "assembl" in title:
        score += 2.5
    elif "genome" in title or "assembl" in title:
        score += 1.5
    elif any(k in text for k in GENOME_KEYWORDS):
        score += 1.0

    # ploidy signal
    if any(k in text for k in PLOIDY_HINTS):
        score += 1.0

    # full text availability
    if paper.get("pmcid") or paper.get("pmCid"):
        score += 2.0

    # organelle penalty (a chloroplast/mito paper must never rank as a strong
    # nuclear-genome candidate, so the title penalty is large)
    if any(k in title for k in ORGANELLE_KEYWORDS):
        score -= 10.0
    elif any(k in abstract for k in ORGANELLE_KEYWORDS):
        score -= 2.0

    return score


def _dedupe_and_rank(candidates: list, identifiers: dict) -> list:
    """Drop duplicate papers across sources, attach relevance_score, sort desc."""
    seen = set()
    out  = []
    for p in candidates:
        key = (
            (p.get("pmid") or "").strip()
            or (p.get("pmcid") or p.get("pmCid") or "").strip()
            or (p.get("doi") or "").strip()
            or (p.get("title", "")[:80].lower())
        )
        if not key or key in seen:
            continue
        seen.add(key)
        p["relevance_score"] = round(score_paper_candidate(p, identifiers), 2)
        out.append(p)
    out.sort(key=lambda x: x["relevance_score"], reverse=True)
    return out


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
    common_name:     str = "",
    max_results:     int = 5,
) -> list:
    genus = scientific_name.split()[0] if scientific_name else ""

    # Multi-identifier name clause: scientific name, plus common name when
    # it is specific enough to be useful (>= 4 chars avoids junk like "fly").
    name_clause = f'"{scientific_name}"'
    if common_name and len(common_name) >= 4:
        name_clause = f'("{scientific_name}" OR "{common_name}")'

    queries = [
        # 1st priority: name(s) + genome assembly
        f'{name_clause} AND "genome assembly"',
        # 2nd priority: name(s) + genome sequence / ploidy / chromosome
        f'{name_clause} AND ("genome sequence" OR ploidy OR chromosome)',
        # 3rd priority: scientific name + genome sequence
        f'"{scientific_name}" AND "genome sequence"',
        # 4th priority: taxon ID fallback (last resort)
        f"TAXONOMY:{taxon_id} AND genome assembly",
    ]

    for query in queries:
        results = search_europe_pmc(query, max_results=max_results * 3)
        if not results:
            continue

        # Keep only results that actually mention the genus — guards against
        # false positives from an ambiguous common name.
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
    scientific_name = assembly.get("scientific_name", "") or ""
    common_name     = assembly.get("common_name", "") or ""
    taxon_id        = assembly.get("taxon_id", "") or ""

    # Mentor's "key metadata fields" — used as multiple retrieval identifiers
    identifiers = {
        "accession":       accession,
        "assembly_name":   assembly.get("assembly_name"),
        "scientific_name": scientific_name,
        "common_name":     common_name,
        "taxon_id":        taxon_id,
    }

    print(f"      taxon_id          = {taxon_id}")
    print(f"      scientific_name   = {scientific_name}")
    print(f"      common_name       = {common_name or '(none)'}")
    print(f"      assembly_name     = {assembly.get('assembly_name')}")
    print(f"      chromosome_number = {assembly.get('chromosome_number')} "
          f"(source: {assembly.get('chromosome_source') or 'not found -> will try text extraction'})")

    candidates = []

    def strongest(cands):
        return max((score_paper_candidate(p, identifiers) for p in cands), default=0.0)

    # Priority 1: directly linked PMIDs from the NCBI assembly record
    linked_pmids = assembly.get("linked_pmids", [])
    if linked_pmids:
        print(f"\n[2/4] Fetching {len(linked_pmids)} directly linked paper(s) ...")
        for pmid in linked_pmids:
            paper = search_by_pmid(pmid)
            if paper:
                paper["retrieval_source"] = "linked_pmid"
                candidates.append(paper)
                print(f"      Found: {paper.get('title', '')[:70]}...")

    # Priority 2: NCBI Entrez elink (curated assembly -> pubmed)
    if not candidates:
        print(f"\n[2/4] Trying NCBI Entrez elink for {accession} ...")
        for p in fetch_linked_pubmed_for_assembly(accession, max_results=max_results):
            p["retrieval_source"] = "elink"
            candidates.append(p)

    # ---- Mode B escalation: if the accession-linked candidates contain no
    #      STRONG genome paper, search by scientific + common name and merge.
    if strongest(candidates) < STRONG_SCORE:
        print(f"\n[2/4] No strong genome paper from accession links "
              f"(best score {strongest(candidates):.1f}) "
              f"-> multi-identifier name search ...")
        for p in search_europe_pmc_by_name(taxon_id, scientific_name, common_name,
                                            max_results=max_results):
            p.setdefault("retrieval_source", "europepmc_name")
            candidates.append(p)

    # Priority 4: NCBI Entrez PMC search — still no strong candidate
    if strongest(candidates) < STRONG_SCORE:
        print(f"\n[2/4] Trying NCBI Entrez PMC search for {scientific_name} ...")
        for p in search_ncbi_entrez(scientific_name, max_results=max_results):
            p.setdefault("retrieval_source", "entrez")
            candidates.append(p)

    # Priority 5: Semantic Scholar — nothing found at all
    if not candidates:
        print(f"\n[2/4] Trying Semantic Scholar for {scientific_name} ...")
        for p in search_semantic_scholar(scientific_name, max_results=max_results):
            p.setdefault("retrieval_source", "semantic_scholar")
            candidates.append(p)

    if not candidates:
        print(f"\n[2/4] All search sources exhausted. No papers found.")

    # ---- de-duplicate across sources + rank best-first by relevance ----
    papers_found = _dedupe_and_rank(candidates, identifiers)
    if papers_found:
        top = papers_found[0]
        print(f"\n      {len(papers_found)} unique candidate(s) ranked; "
              f"top score {top['relevance_score']} ({top.get('retrieval_source')})")

    print(f"\n[3/4] Validating IDs and fetching text ...")
    enriched_papers = []
    for paper in papers_found:
        validation = validate_paper_ids(paper)
        text_data  = get_best_available_text(paper)
        enriched_papers.append({
            "title":            paper.get("title", ""),
            "pmcid":            paper.get("pmcid"),
            "pmid":             paper.get("pmid"),
            "doi":              paper.get("doi"),
            "retrieval_source": paper.get("retrieval_source"),
            "relevance_score":  paper.get("relevance_score"),
            "validation":       validation,
            "text_data":        text_data,
        })
        print(f"      [{validation['best_available']:>10}]  "
              f"(score {paper.get('relevance_score')}, {paper.get('retrieval_source')})  "
              f"{paper.get('title', '')[:52]}...")

    print(f"\n[4/4] Done. {len(enriched_papers)} paper(s) ready for extraction.")
    print(f"      chromosome_number will be "
          f"{'used from NCBI' if assembly.get('chromosome_number') else 'extracted from text in extract.py'}")

    return {
        "assembly": assembly,
        "papers":   enriched_papers,
    }