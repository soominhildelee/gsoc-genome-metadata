import os
import re
import json
import requests

# ---- controlled vocabulary (shared with extract.py) ----
try:
    from extract import _LEVEL_LABEL
except Exception:
    _LEVEL_LABEL = {1: "monoploid", 2: "diploid", 3: "triploid", 4: "tetraploid",
                    5: "pentaploid", 6: "hexaploid", 8: "octoploid"}

VALID_MECHANISMS = {"autopolyploid", "allopolyploid",
                    "amphidiploid", "segmental_allopolyploid"}

# ---- config ----
_DEFAULT_MODEL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "models", "gemma-2-2b-it-Q4_K_M.gguf"
)
BASE_URL  = os.environ.get("GEMMA_BASE_URL", "http://localhost:8000/v1")
MODEL     = os.environ.get("GEMMA_MODEL", _DEFAULT_MODEL)
TIMEOUT   = int(os.environ.get("GEMMA_TIMEOUT", "300"))
MAX_CHARS = 6000   # cap passage text sent to the model


def _server_is_up() -> bool:
    try:
        requests.get(f"{BASE_URL}/models", timeout=3)
        return True
    except Exception:
        return False


def is_enabled() -> bool:
    env = os.environ.get("GEMMA_ENABLED")
    if env == "1":
        return True
    if env == "0":
        return False
    # Auto-detect: enabled if the local server is reachable
    return _server_is_up()


# JSON schema used for vLLM guided decoding (forces well-formed output).
_PLOIDY_SCHEMA = {
    "type": "object",
    "properties": {
        "ploidy_level":      {"type": ["integer", "null"]},
        "mechanism":         {"type": ["string", "null"]},
        "evidence_quote":    {"type": ["string", "null"]},
        "stated_explicitly": {"type": "boolean"},
    },
    "required": ["ploidy_level", "mechanism", "evidence_quote", "stated_explicitly"],
}

_SYSTEM = (
    "You are a careful genomics curator. You extract ploidy ONLY from the text "
    "passages provided. You never use outside knowledge. If the passages do not "
    "EXPLICITLY state the organism's ploidy, you return null. For every value you "
    "return, you must copy the exact sentence you used as evidence into "
    "'evidence_quote' (verbatim, from the passages). Output JSON only."
)


def _build_user_prompt(species: str, passages: list) -> str:
    blocks = []
    total = 0
    for i, p in enumerate(passages, 1):
        t = (p.get("text") or "").strip()
        if not t:
            continue
        if total + len(t) > MAX_CHARS:
            t = t[: max(0, MAX_CHARS - total)]
        blocks.append(f"[Passage {i} | section: {p.get('section', '?')}]\n{t}")
        total += len(t)
        if total >= MAX_CHARS:
            break
    joined = "\n\n".join(blocks) if blocks else "(no passages)"
    return (
        f"Organism: {species or 'unknown'}\n\n"
        f"Passages:\n{joined}\n\n"
        "Task: Determine the ploidy LEVEL of THIS organism (an integer x-level: "
        "diploid=2, tetraploid=4, hexaploid=6, ...). Do NOT report the ploidy of "
        "ancestor / progenitor / related species. If a polyploidy mechanism is "
        "stated, set 'mechanism' to one of: autopolyploid, allopolyploid, "
        "amphidiploid, segmental_allopolyploid; otherwise null.\n"
        "Rules:\n"
        "- If ploidy is not explicitly stated for this organism, set ploidy_level "
        "to null and stated_explicitly to false.\n"
        "- 'evidence_quote' must be copied verbatim from a passage above.\n"
        "Return JSON with keys: ploidy_level, mechanism, evidence_quote, "
        "stated_explicitly."
    )


def _build_messages(species: str, passages: list) -> list:
    user_content = _build_user_prompt(species, passages)
    # Gemma-2 chat template raises an exception for system role, so we merge
    # the system instruction into the first user turn for Gemma-based models.
    if "gemma" in MODEL.lower():
        return [{"role": "user", "content": f"{_SYSTEM}\n\n{user_content}"}]
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": user_content},
    ]


def _call_vllm(species: str, passages: list) -> dict | None:
    payload = {
        "model": MODEL,
        "messages": _build_messages(species, passages),
        "temperature": 0,
        "max_tokens": 400,
        # vLLM extension: constrain output to the schema (ignored by plain OpenAI).
        "guided_json": _PLOIDY_SCHEMA,
        # llama-cpp-python / standard OpenAI fallback for JSON mode.
        "response_format": {"type": "json_object"},
    }
    try:
        r = requests.post(f"{BASE_URL}/chat/completions", json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, ValueError, IndexError) as e:
        print(f"  [gemma] call failed ({e}); skipping Gemma layer")
        return None
    return _parse_json(content)


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    # grab the first {...} block if the model added prose
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if m:
        t = m.group()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _is_grounded(quote: str, passages: list) -> bool:
    q = _norm(quote)
    if len(q) < 12:          # too short to be a meaningful, checkable quote
        return False
    corpus = _norm(" ".join(p.get("text", "") for p in passages))
    return q in corpus


def _validate(obj: dict, passages: list) -> dict | None:
    if not isinstance(obj, dict):
        return None
    level     = obj.get("ploidy_level")
    mechanism = obj.get("mechanism")
    quote     = obj.get("evidence_quote") or ""
    explicit  = bool(obj.get("stated_explicitly"))

    # normalise level
    if isinstance(level, str):
        m = re.search(r"\d+", level)
        level = int(m.group()) if m else None
    if not isinstance(level, int):
        level = None

    # mechanism must be in the controlled vocabulary
    if mechanism not in VALID_MECHANISMS:
        mechanism = None

    # grounding gate — if the claimed value is not backed by a real quote, drop it
    grounded = _is_grounded(quote, passages)
    if (level is not None or mechanism is not None):
        if not explicit or not grounded:
            print("  [gemma] value rejected (not explicit / not grounded in passages)")
            level, mechanism = None, None

    # schema normalisation (mirror resolve_ploidy_fields)
    if level == 1:
        mechanism = None
    elif level is not None and level < 3:
        mechanism = None
    if mechanism == "amphidiploid" and level is not None and level % 2 != 0:
        mechanism = None     # amphidiploid implies an even level

    if level is None and mechanism is None:
        return None

    return {
        "level":       level,
        "mechanism":   mechanism,
        "irregular":   False,
        "derivation":  None,
        "tuple":       [level, mechanism, False, None],
        "level_label": _LEVEL_LABEL.get(level) if level is not None else None,
        "status":      "found_in_paper",
        "source":      "gemma",
        "confidence":  0.7,
        "method":      "gemma",
        "evidence":    quote.strip()[:300],
    }


def run_gemma_ploidy(species: str, evidence_passages: list) -> dict | None:
    if not is_enabled():
        return None
    if not evidence_passages:
        return None
    print("\n  [gemma] querying local Gemma for ploidy (grounded extraction) ...")
    raw = _call_vllm(species, evidence_passages)
    if raw is None:
        return None
    return _validate(raw, evidence_passages)


def combine_with_gemma(ensemble: dict, gemma: dict | None) -> dict:
    if not gemma:
        return ensemble

    out = dict(ensemble)
    e_lvl, g_lvl = ensemble.get("level"), gemma.get("level")

    if e_lvl is not None and g_lvl is not None and e_lvl == g_lvl:
        out["confidence"] = min(round((ensemble.get("confidence", 0.5) + 0.2), 2), 0.98)
        out["method"]     = "consensus_3way"
        # prefer a non-null, more specific mechanism
        if not out.get("mechanism") and gemma.get("mechanism"):
            out["mechanism"] = gemma["mechanism"]
            out["tuple"]     = [out["level"], out["mechanism"],
                                out.get("irregular", False), out.get("derivation")]
        out["gemma"] = {"level": g_lvl, "mechanism": gemma.get("mechanism"),
                        "evidence": gemma.get("evidence")}

    elif e_lvl is None and g_lvl is not None:
        out = dict(gemma)
        out["method"] = "gemma_only"
        out["confidence"] = round(gemma.get("confidence", 0.7) * 0.9, 2)

    elif e_lvl is not None and g_lvl is not None and e_lvl != g_lvl:
        out["method"] = f"conflict_rule_vector_vs_gemma ({e_lvl} vs {g_lvl})"
        out["confidence"] = round(ensemble.get("confidence", 0.5) * 0.8, 2)
        out["gemma"] = {"level": g_lvl, "mechanism": gemma.get("mechanism"),
                        "evidence": gemma.get("evidence")}

    return out