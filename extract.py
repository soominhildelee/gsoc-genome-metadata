import re
from typing import Optional

# ============================================================
# Section weights
# ============================================================

SECTION_WEIGHTS = {
    "title":                2.5,  # paper title — most authoritative for ploidy
    "abstract":             2.0,
    "introduction":         1.5,
    "background":           1.5,
    "results":              1.2,
    "discussion":           1.0,
    "methods":              0.8,
    "materials":            0.8,
    "conclusions":          0.7,
    "supplementary":        0.3,
    "acknowledgements":     0.1,
    "author contributions": 0.0,  # noise — skip entirely
    "data availability":    0.0,  # noise — skip entirely
}

def get_section_weight(section_name: str) -> float:
    s = section_name.lower()
    for key, weight in SECTION_WEIGHTS.items():
        if key in s:
            return weight
    return 1.0  # default


PRIORITY_SECTIONS = ["abstract", "introduction", "background"]


# ============================================================
# STEP 1 — Extract ploidy from text
# ============================================================
#
# Why this is more than a keyword count:
#   Polyploid genome papers describe their DIPLOID ANCESTORS far more
#   often than they state the organism's own ploidy. A naive frequency
#   vote therefore picks "diploid" for an allotetraploid (e.g. peanut).
#
# Strategy:
#   1. `2n = Nx = M` chromosome formula  -> strongest, decisive evidence.
#   2. Compound terms (allo/auto-ploid)  -> the author is classifying THIS
#                                            organism's polyploidy -> strong.
#   3. A bare diploid/polyploid mention inside an ancestor / progenitor /
#      donor / subgenome context describes a RELATIVE, not the assembled
#      organism -> excluded from the vote.
# ============================================================

# Base ploidy words -> normalised level
_PLOIDY_BASES = {
    "haploid":    "haploid",
    "diploid":    "diploid",
    "triploid":   "triploid",
    "tetraploid": "tetraploid",
    "pentaploid": "pentaploid",
    "hexaploid":  "hexaploid",
    "heptaploid": "heptaploid",
    "octoploid":  "octoploid",
    "decaploid":  "decaploid",
}

# x-coefficient from "2n = Nx = M" -> ploidy level
_X_TO_PLOIDY = {
    1: "haploid",  2: "diploid",   3: "triploid",  4: "tetraploid",
    5: "pentaploid", 6: "hexaploid", 7: "heptaploid", 8: "octoploid",
    10: "decaploid",
}

# Single unified ploidy-word matcher (optional allo/auto/aneu/eu prefix).
# Word boundaries + optional trailing y/s avoid matching inside
# "diploidization" while still catching "diploids" / "diploidy".
_PLOIDY_WORD_RE = re.compile(
    r"\b(allo|auto|aneu|eu)?[\-\s]?"
    r"(haploid|diploid|triploid|tetraploid|pentaploid|hexaploid|heptaploid|octoploid|decaploid)"
    r"(?:y|s)?\b",
    re.IGNORECASE,
)

# 2n = Nx = M  -> the N coefficient is the ploidy level (most reliable)
_CHROM_FORMULA_RE = re.compile(r"2n\s*=\s*(\d+)\s*x\s*=\s*\d+", re.IGNORECASE)

# Things that look like ploidy but are not a ploidy-level statement
_FALSE_POSITIVE_PATTERNS = [
    r"doubled?\s+haploid",      # experimental technique
    r"haploid\s+genotype",
    r"haplome",
    r"haploid\s+assembl",       # "haploid assembly/representation"
    r"haploid\s+representation",
    r"haploid\s+number",        # chromosome count statement, not ploidy
    r"haploid\s+coverage",
]

# When a diploid/polyploid term sits in one of these contexts it is
# describing an ancestor / progenitor / relative — NOT the assembled
# organism — and must be excluded from the organism-level vote.
_ANCESTOR_CONTEXT_RE = re.compile(
    r"ancestor|ancestral|progenitor|parental|\brelativ|\bdonor|"
    r"sub-?genome|derived\s+from|descend|two\s+diploid|both\s+diploid|"
    r"other\s+diploid|related\s+diploid|diploid\s+relativ|"
    r"diploid\s+ancestor|diploid\s+progenitor|diploid\s+wild|wild\s+diploid|"
    r"each\s+diploid",
    re.IGNORECASE,
)


def _is_false_positive(ctx: str) -> bool:
    return any(re.search(p, ctx, re.IGNORECASE) for p in _FALSE_POSITIVE_PATTERNS)


def find_ploidy_mentions(text: str, window: int = 50) -> list[dict]:
    """Scan text once and classify every ploidy mention.

    Shared by extract.py (rule-based) and search.py (vector) so both
    stages reach the same judgement. Returns a list of dicts:
        {value, match, context, is_compound, is_ancestor, is_formula}
    """
    mentions: list[dict] = []

    # --- 2n = Nx = M  (definitive) ---
    for m in _CHROM_FORMULA_RE.finditer(text):
        level = _X_TO_PLOIDY.get(int(m.group(1)))
        if level is None:
            continue
        ctx = text[max(0, m.start() - window): m.end() + window]
        if _is_false_positive(ctx):
            continue
        mentions.append({
            "value":       level,
            "match":       m.group().strip(),
            "context":     ctx,
            "is_compound": True,    # organism-level, decisive
            "is_ancestor": False,
            "is_formula":  True,
        })

    # --- ploidy words ---
    for m in _PLOIDY_WORD_RE.finditer(text):
        prefix = (m.group(1) or "").lower()
        base   = m.group(2).lower()
        level  = _PLOIDY_BASES.get(base)
        if level is None:
            continue
        ctx = text[max(0, m.start() - window): m.end() + window]
        if _is_false_positive(ctx):
            continue
        is_compound = prefix in ("allo", "auto")
        # compound (allo/auto-X) always describes the organism itself
        is_ancestor = (not is_compound) and bool(_ANCESTOR_CONTEXT_RE.search(ctx))
        mentions.append({
            "value":       level,
            "match":       m.group().strip(),
            "context":     ctx,
            "is_compound": is_compound,
            "is_ancestor": is_ancestor,
            "is_formula":  False,
        })

    return mentions


# Evidence weighting
_COMPOUND_BOOST = 6.0    # allo/auto-ploid: explicit organism classification
_FORMULA_BOOST  = 10.0   # 2n = Nx: direct, unambiguous


def extract_ploidy(weighted_sections: dict) -> dict:
    counts:  dict[str, float] = {}
    best_ev: dict[str, dict]  = {}
    n_organism = 0
    n_ancestor = 0
    n_total    = 0

    for sec_name, (text, weight) in weighted_sections.items():
        if weight == 0.0:
            continue  # skip noise sections

        is_priority = any(p in sec_name.lower() for p in PRIORITY_SECTIONS)

        for men in find_ploidy_mentions(text):
            n_total += 1

            # Ancestor/progenitor mention -> not the assembled organism
            if men["is_ancestor"]:
                n_ancestor += 1
                continue

            n_organism += 1

            if men["is_formula"]:
                w = weight * _FORMULA_BOOST
            elif men["is_compound"]:
                w = weight * _COMPOUND_BOOST
            else:
                w = weight

            counts[men["value"]] = counts.get(men["value"], 0.0) + w

            # Keep the strongest evidence: formula > compound > priority section
            rank_new = (men["is_formula"], men["is_compound"], is_priority)
            cur = best_ev.get(men["value"])
            if cur is None or rank_new > cur["_rank"]:
                best_ev[men["value"]] = {
                    "context":     men["context"],
                    "section":     sec_name,
                    "match":       men["match"],
                    "is_priority": is_priority,
                    "_rank":       rank_new,
                }

    if not counts:
        return {
            "value":           "unknown",
            "confidence":      0.0,
            "evidence_count":  n_total,
            "weighted_counts": {},
            "best_evidence":   "",
            "best_section":    "",
            "method":          "no_text_evidence",
        }

    best  = max(counts, key=counts.get)
    total = sum(counts.values())

    base_conf      = counts[best] / total
    priority_bonus = 0.1 if best_ev.get(best, {}).get("is_priority") else 0.0
    confidence     = min(round(base_conf + priority_bonus, 2), 0.95)

    return {
        "value":           best,
        "confidence":      confidence,
        "evidence_count":  n_organism,
        "weighted_counts": {k: round(v, 2) for k, v in counts.items()},
        "best_evidence":   best_ev.get(best, {}).get("context", ""),
        "best_section":    best_ev.get(best, {}).get("section", ""),
        "method":          "text_weighted_vote",
    }


# ============================================================
# Reference ploidy fallback (OPTIONAL, transparently sourced)
# ============================================================
#
# Used ONLY when neither the paper text nor NCBI yields a ploidy.
# These are well-established, textbook ploidies keyed by NCBI taxon_id.
# Output is clearly marked  method="reference_taxon"  with modest
# confidence so downstream review can see it did NOT come from the paper.
#
# NOTE: This is a curated reference, not a place to hardcode test answers.
#       Expand it from a maintained source if you keep it; otherwise delete
#       this dict and the fallback block in extract_metadata() to make the
#       tool return "unknown" when the text is silent (the honest default).
# ============================================================

KNOWN_PLOIDY: dict[str, str] = {
    "3702":  "diploid",     # Arabidopsis thaliana
    "4081":  "diploid",     # Solanum lycopersicum (tomato)
    "4577":  "diploid",     # Zea mays
    "39947": "diploid",     # Oryza sativa Japonica
    "3818":  "tetraploid",  # Arachis hypogaea (peanut)
    "4565":  "hexaploid",   # Triticum aestivum (bread wheat)
}


# ============================================================
# STEP 2 — Extract chromosome number from text
# ============================================================

CHROMOSOME_PATTERNS = [
    r"2n\s*=\s*(\d+)",            # 2n = 48
    r"2n\s*=\s*\d+x\s*=\s*(\d+)", # 2n = 4x = 48
    r"n\s*=\s*(\d+)",             # n = 24 (haploid)
    r"(\d+)\s+chromosome",        # 48 chromosomes
    r"chromosome\s+number\s+(?:of\s+)?(\d+)",
]

def extract_chromosome_number(weighted_sections: dict) -> dict:
    # Search priority sections first
    priority_order = sorted(
        weighted_sections.items(),
        key=lambda x: x[1][1],  # sort by weight descending
        reverse=True
    )

    for sec_name, (text, weight) in priority_order:
        if weight == 0.0:
            continue
        for pattern in CHROMOSOME_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                number = match.group(1)
                ctx_start = max(0, match.start() - 50)
                ctx_end   = min(len(text), match.end() + 50)
                return {
                    "value":    number,
                    "evidence": text[ctx_start:ctx_end],
                    "section":  sec_name,
                    "source":   "text_extraction",
                }

    return {"value": None, "evidence": "", "source": "text_extraction"}


# ============================================================
# STEP 3 — Extract cultivar / strain names from text
# ============================================================

CULTIVAR_PATTERNS = [
    r"cultivar\s+['\u2018\u2019\"]([^'\u2018\u2019\"]+)['\u2018\u2019\"]",
    r"cv\.\s*['\u2018\u2019\"]?([A-Z][a-zA-Z\s]+)",
    r"['\u2018\u2019]([A-Z][a-zA-Z\s]{2,25})['\u2018\u2019]",
    r"strain\s+([A-Z][a-zA-Z0-9\-]+)",
    r"ecotype\s+([A-Z][a-zA-Z0-9\-]+)",
]

CULTIVAR_STOPWORDS = {
    "the", "and", "for", "this", "that", "with", "from",
    "table", "figure", "supplementary", "data", "note",
    "results", "methods", "background", "abstract",
    "asm", "bam", "fasta", "fastq", "csv", "tsv", "json", "xml",
    "hifiasm", "busco", "samtools", "orthofinder", "blast", "minimap",
    "viridiplantae", "embryophyta", "rosaceae", "eudicots",
    "haplome", "haplotype", "genbank", "refseq", "bioproject",
}

def extract_cultivars(filtered_text: str) -> list[str]:
    cultivars = set()
    for pattern in CULTIVAR_PATTERNS:
        for match in re.findall(pattern, filtered_text, re.IGNORECASE):
            name = match.strip()
            if 2 < len(name) <= 30 and name.lower() not in CULTIVAR_STOPWORDS:
                cultivars.add(name)
    return sorted(cultivars)


# ============================================================
# STEP 4 — Extract sex from text
# ============================================================

SEX_PATTERNS = [
    r"(female|male)\s+(individual|specimen|plant|animal|organism)",
    r"sex[:\s]+(male|female|hermaphrodite)",
    r"(maternal|paternal)\s+",
]

def extract_sex(weighted_sections: dict) -> dict:
    for sec_name, (text, weight) in weighted_sections.items():
        if weight == 0.0:
            continue
        for pattern in SEX_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return {
                    "value":    match.group(1).lower(),
                    "section":  sec_name,
                    "evidence": text[max(0, match.start()-30):match.end()+30],
                }
    return {"value": "unknown", "evidence": ""}


# ============================================================
# STEP 5 — Extract species name from text
# ============================================================

def extract_species(sections: dict, weighted_sections: dict) -> Optional[str]:
    pattern1 = r"([A-Z][a-z]+\s+[a-z]+)(?:\s+(?:Borkh\.|L\.|var\.))"
    pattern2 = r"([A-Z][a-z]{2,15}\s+[a-z]{2,15})"

    # Priority 1: Abstract
    abstract = sections.get("Abstract", "")
    for pat in [pattern1, pattern2]:
        m = re.search(pat, abstract)
        if m:
            return m.group(1)

    # Priority 2: Introduction / Background (weight >= 1.5)
    for sec_name, (text, weight) in weighted_sections.items():
        if weight < 1.5:
            continue
        for pat in [pattern1, pattern2]:
            m = re.search(pat, text)
            if m:
                return m.group(1)

    # Priority 3: remaining filtered text
    filtered = " ".join(t for _, (t, w) in weighted_sections.items() if w > 0)
    m = re.search(pattern2, filtered)
    if m:
        return m.group(1)

    return "unknown"


# ============================================================
# MAIN FUNCTION — run all steps above in order
#           input:  parsed dict from parse.py
#                   assembly dict from fetch.py
#           output: all extracted metadata fields
# ============================================================

def extract_metadata(parsed: dict, assembly: dict, paper_title: str = None) -> dict:
    sections = dict(parsed.get("sections", {}))

    # Paper title is the most authoritative source for ploidy
    # (e.g. "...segmental allotetraploid peanut...", "hexaploid bread wheat").
    # It is often NOT part of the fetched abstract/fulltext body, so inject it.
    if paper_title:
        sections["Title"] = paper_title

    # Build weighted sections dict once — reused by all extractors
    weighted_sections = {
        sec: (text, get_section_weight(sec))
        for sec, text in sections.items()
    }

    # Filtered text — noise sections removed
    filtered_text = " ".join(
        text for sec, text in sections.items()
        if get_section_weight(sec) > 0
    )

    print("  [extract] Running rule-based extraction ...")

    # ── Fields from NCBI (no text extraction needed) ──────────
    ncbi_accession    = assembly.get("assembly_accession")
    ncbi_name         = assembly.get("assembly_name")
    ncbi_taxon        = assembly.get("taxon_id")
    ncbi_species      = assembly.get("scientific_name")
    ncbi_chrom        = assembly.get("chromosome_number")
    ncbi_chrom_source = assembly.get("chromosome_source")

    # ── Species: NCBI first, text fallback ────────────────────
    if ncbi_species:
        species = {"value": ncbi_species, "source": "ncbi"}
    else:
        species = {
            "value":  extract_species(sections, weighted_sections),
            "source": "text_extraction",
        }

    # ── Chromosome number: NCBI first, text fallback ──────────
    if ncbi_chrom:
        chromosome_number = {
            "value":  str(ncbi_chrom),
            "source": ncbi_chrom_source,
        }
    else:
        print("  [extract] chromosome_number not in NCBI → extracting from text ...")
        chromosome_number = extract_chromosome_number(weighted_sections)

    # ── Fields only available from text ───────────────────────
    ploidy    = extract_ploidy(weighted_sections)
    cultivars = extract_cultivars(filtered_text)
    sex       = extract_sex(weighted_sections)

    # ── Ploidy reference fallback (text + NCBI both silent) ───
    #     Remove this block if you prefer "unknown" when the paper
    #     does not state ploidy (see KNOWN_PLOIDY note above).
    if ploidy["value"] == "unknown" and str(ncbi_taxon) in KNOWN_PLOIDY:
        ref_value = KNOWN_PLOIDY[str(ncbi_taxon)]
        print(f"  [extract] ploidy not in text → reference fallback: "
              f"{ref_value} (taxon {ncbi_taxon})")
        ploidy = {
            "value":           ref_value,
            "confidence":      0.6,
            "evidence_count":  ploidy.get("evidence_count", 0),
            "weighted_counts": {},
            "best_evidence":   f"(reference DB: taxon {ncbi_taxon} documented as {ref_value})",
            "best_section":    "reference_db",
            "method":          "reference_taxon",
        }

    print(f"  [extract] Done.")
    print(f"            species           = {species['value']} (source: {species['source']})")
    print(f"            ploidy            = {ploidy['value']} "
          f"(confidence: {ploidy['confidence']}, method: {ploidy.get('method')})")
    print(f"            chromosome_number = {chromosome_number['value']} (source: {chromosome_number['source']})")
    print(f"            cultivars         = {cultivars}")
    print(f"            sex               = {sex['value']}")

    return {
        # NCBI-sourced fields
        "assembly_accession": ncbi_accession,
        "assembly_name":      ncbi_name,
        "taxon_id":           ncbi_taxon,
        # Species (NCBI or text)
        "species":            species,
        # Text-extracted fields
        "ploidy":             ploidy,
        "chromosome_number":  chromosome_number,
        "cultivars":          cultivars,
        "sex":                sex,
    }