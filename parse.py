import re
from xml.etree import ElementTree as ET

_TEX_BLOCK = re.compile(r"\\documentclass.*?\\end\{document\}", re.DOTALL)
_TEX_CMD   = re.compile(r"\\[a-zA-Z]+\s*(\[[^\]]*\])?(\{[^}]*\})?")
_WS        = re.compile(r"[ \t\r\n\f\v]+")


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = _TEX_BLOCK.sub(" ", text)
    text = _TEX_CMD.sub(" ", text)
    text = text.replace("$$", " ").replace("$", " ")
    return _WS.sub(" ", text).strip()


def detect_text_type(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("<"):
        return "xml"
    return "abstract"


def _serialize_table(table_wrap) -> str:
    table = table_wrap.find(".//table")
    if table is None:
        return ""

    headers = []
    thead = table.find(".//thead")
    header_row = thead.find(".//tr") if thead is not None else None
    if header_row is None:
        header_row = table.find(".//tr")
    if header_row is not None:
        headers = [
            clean_text("".join(c.itertext()))
            for c in list(header_row) if c.tag in ("th", "td")
        ]

    tbody = table.find(".//tbody")
    row_elems = tbody.findall(".//tr") if tbody is not None else table.findall(".//tr")

    rows = []
    for tr in row_elems:
        cells = [
            clean_text("".join(c.itertext()))
            for c in list(tr) if c.tag in ("th", "td")
        ]
        if not any(cells):
            continue
        if headers and len(headers) == len(cells):
            pair = " | ".join(f"{h}: {v}" for h, v in zip(headers, cells) if v)
        else:
            pair = " | ".join(v for v in cells if v)
        if pair:
            rows.append(pair)

    return " ; ".join(rows[:40])


def parse_xml_tables(root) -> dict[str, str]:
    tables = {}
    for i, tw in enumerate(root.iter("table-wrap"), 1):
        label_elem = tw.find("label")
        label = label_elem.text.strip() if (label_elem is not None and label_elem.text) else ""

        caption_texts = []
        for cap in tw.iter("caption"):
            for p in cap.iter("p"):
                t = clean_text("".join(p.itertext()))
                if t:
                    caption_texts.append(t)
        caption = " ".join(caption_texts)

        body = clean_text(f"{caption} {_serialize_table(tw)}")
        if not body:
            continue

        title = label or f"Table {i}"
        if title in tables and len(tables[title]) >= len(body):
            continue
        tables[title] = body

    return tables


def parse_xml_sections(xml_text: str) -> dict[str, str]:
    sections = {}
    try:
        root = ET.fromstring(xml_text)

        for abstract in root.iter("abstract"):
            texts = []
            for p in abstract.iter("p"):
                full_text = clean_text("".join(p.itertext()))
                if full_text:
                    texts.append(full_text)
            if texts:
                sections["Abstract"] = " ".join(texts)

        for sec in root.iter("sec"):
            title_elem = sec.find("title")
            if title_elem is None or not title_elem.text:
                continue
            section_title = title_elem.text.strip()
            texts = []
            for p in sec.iter("p"):
                full_text = clean_text("".join(p.itertext()))
                if full_text:
                    texts.append(full_text)
            if texts:
                existing = sections.get(section_title, "")
                merged = " ".join(texts)
                sections[section_title] = merged if len(merged) > len(existing) else existing

        for title, body in parse_xml_tables(root).items():
            sections[title] = body

    except ET.ParseError as e:
        print(f"  [parse] XML parse error: {e}")

    return sections


def parse_abstract(abstract_text: str) -> dict[str, str]:
    return {"Abstract": clean_text(abstract_text)}


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

        if current_chunk.strip():
            chunks.append({
                "section":     sec_title,
                "text":        current_chunk.strip(),
                "chunk_index": chunk_index,
            })

    return chunks


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

    text_type = detect_text_type(text)
    if text_type == "xml":
        sections = parse_xml_sections(text)
        print(f"  [parse] XML parsed → {len(sections)} sections")
    else:
        sections = parse_abstract(text)
        print(f"  [parse] Plain abstract wrapped as single section")

    chunks = split_into_chunks(sections)
    print(f"  [parse] {len(chunks)} chunks ready for extract.py and search.py")

    return {
        "source":     source,
        "sections":   sections,
        "chunks":     chunks,
        "n_sections": len(sections),
        "n_chunks":   len(chunks),
    }