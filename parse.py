from xml.etree import ElementTree as ET

# ============================================================
# STEP 1 — Detect input type
# ============================================================

def detect_text_type(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("<"):
        return "xml"
    return "abstract"

# ============================================================
# STEP 2 — Parse XML into sections
# ============================================================

def parse_xml_sections(xml_text: str) -> dict[str, str]:
    sections = {}
    try:
        root = ET.fromstring(xml_text)

        # Extract abstract separately — always highest priority
        for abstract in root.iter("abstract"):
            texts = []
            for p in abstract.iter("p"):
                full_text = "".join(p.itertext()).strip()
                if full_text:
                    texts.append(full_text)
            if texts:
                sections["Abstract"] = " ".join(texts)

        # Extract named sections from <sec> tags
        for sec in root.iter("sec"):
            title_elem = sec.find("title")
            if title_elem is None or not title_elem.text:
                continue
            section_title = title_elem.text.strip()
            texts = []
            for p in sec.iter("p"):
                full_text = "".join(p.itertext()).strip()
                if full_text:
                    texts.append(full_text)
            if texts:
                # Keep the longest version if section title appears twice
                existing = sections.get(section_title, "")
                merged = " ".join(texts)
                sections[section_title] = merged if len(merged) > len(existing) else existing

    except ET.ParseError as e:
        print(f"  [parse] XML parse error: {e}")

    return sections

# ============================================================
# STEP 3 — Wrap plain abstract as a single-section dict
# ============================================================

def parse_abstract(abstract_text: str) -> dict[str, str]:
    return {"Abstract": abstract_text.strip()}

# ============================================================
# STEP 4 — Split sections into smaller chunks for vector search
# ============================================================

def split_into_chunks(sections: dict[str, str], chunk_size: int = 300) -> list[dict]:
    chunks = []
    for sec_title, text in sections.items():
        sentences = text.replace(". ", ".|").split("|")
        current_chunk = ""
        chunk_index = 0

        for sent in sentences:
            if len(current_chunk) + len(sent) < chunk_size:
                current_chunk += sent + " "
            else:
                if current_chunk.strip():
                    chunks.append({
                        "section":     sec_title,
                        "text":        current_chunk.strip(),
                        "chunk_index": chunk_index,
                    })
                    chunk_index += 1
                current_chunk = sent + " "

        # Add the last remaining chunk
        if current_chunk.strip():
            chunks.append({
                "section":     sec_title,
                "text":        current_chunk.strip(),
                "chunk_index": chunk_index,
            })

    return chunks


# ============================================================
# MAIN FUNCTION — run all steps above in order
#           input:  text_data dict from fetch.py
#                   (keys: source, text)
#           output: {
#                       sections: {section_title: text},
#                       chunks:   [{section, text, chunk_index}],
#                       source:   "fulltext" | "abstract" | "none"
#                   }
# ============================================================

def parse_paper(text_data: dict) -> dict:
    source = text_data.get("source", "none")
    text   = text_data.get("text")

    if not text:
        print("  [parse] No text available — returning empty result")
        return {
            "source":     source,
            "sections":   {},
            "chunks":     [],
            "n_sections": 0,
            "n_chunks":   0,
        }

    # Detect and parse
    text_type = detect_text_type(text)
    if text_type == "xml":
        sections = parse_xml_sections(text)
        print(f"  [parse] XML parsed → {len(sections)} sections")
    else:
        sections = parse_abstract(text)
        print(f"  [parse] Plain abstract wrapped as single section")

    # Split into chunks for vector search
    chunks = split_into_chunks(sections)

    print(f"  [parse] {len(chunks)} chunks ready for extract.py and search.py")

    return {
        "source":     source,
        "sections":   sections,
        "chunks":     chunks,
        "n_sections": len(sections),
        "n_chunks":   len(chunks),
    }