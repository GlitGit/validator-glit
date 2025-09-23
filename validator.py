import pdfplumber, re, json, yaml
from pathlib import Path
import pandas as pd  # for Excel export

CFG_DIR = Path("vendors")
GLOBAL_CFG = CFG_DIR / "global.yaml"
SAMPLES_DIR = Path("samples")

# ---------- utilities ----------
def load_yaml(p):
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def read_pdf_lines(pdf_path: Path):
    """Read all lines from a PDF (text-based, not OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        lines = []
        for pg in pdf.pages:
            text = pg.extract_text() or ""
            lines.extend([ln.strip() for ln in text.splitlines() if ln.strip()])
        return lines

def find_first_match(patterns, text):
    if not patterns:
        return None
    low = text.lower()
    for p in patterns:
        if p.lower() in low:
            return p
    return None

def header_zone_guess(lines, n=8):
    """Look at the first n lines of page 1 and guess a vendor name in ALLCAPS."""
    header = " ".join(lines[:max(n, 1)])
    caps = re.findall(r"\b[A-Z][A-Z&\s]{2,}\b", header)
    return (sorted(caps, key=len, reverse=True)[0].strip() if caps else None)

def extract_remit_block(lines, remit_headers):
    """Extract a block of text following a 'Remit To' or similar header."""
    for i, ln in enumerate(lines):
        if any(h.lower() in ln.lower() for h in (remit_headers or [])):
            return " ".join(lines[i:i+7])
    return ""

# ---------- vendor detection ----------
def detect_vendor_id(lines, vendor_cfgs, global_cfg):
    joined = " ".join(lines)

    # 1) explicit match on keywords/domains/address
    for v in vendor_cfgs:
        vk = v.get("vendor_keywords", {})
        if find_first_match(vk.get("names"), joined) or \
           find_first_match(vk.get("domains"), joined) or \
           find_first_match(vk.get("address_snippets"), joined):
            return v["id"], "keywords"

    # 2) remit-to block
    remit = extract_remit_block(lines, global_cfg["vendor_detection"]["remit_headers"])
    if remit:
        for v in vendor_cfgs:
            vk = v.get("vendor_keywords", {})
            if find_first_match(vk.get("names"), remit) or \
               find_first_match(vk.get("domains"), remit) or \
               find_first_match(vk.get("address_snippets"), remit):
                return v["id"], "remit_block"

    # 3) header zone (top-of-page ALLCAPS)
    guess = header_zone_guess(lines, global_cfg["vendor_detection"]["header_zone_lines"])
    if guess:
        gl = guess.lower()
        for v in vendor_cfgs:
            for nm in v.get("vendor_keywords", {}).get("names", []):
                if nm.lower() in gl or gl in nm.lower():
                    return v["id"], "header_zone"

    return "global", "fallback"

# ---------- invoice extraction ----------
def merge_invoice_field(vendor_cfg, global_cfg):
    g = global_cfg["fields"]["invoice_number"]
    v = vendor_cfg.get("fields", {}).get("invoice_number", {})
    return {
        "anchors": v.get("anchors", g["anchors"]),
        "regex":   v.get("regex",   g["regex"]),
    }

def pick_invoice_number(lines, anchors, regex):
    rx = re.compile(regex)

    for i, ln in enumerate(lines):
        low = ln.lower()
        hits = [a for a in anchors if a.lower() in low]
        if hits:
            # prefer match that appears AFTER the first anchor on that line
            a = min(hits, key=lambda x: low.find(x.lower()))
            start = low.find(a.lower())
            segment = ln[start + len(a):]  # text to the right of anchor
            m = rx.search(segment)
            if m:
                return m.group(0), "same_line_after_anchor"
            # else try next line
            if i + 1 < len(lines):
                m2 = rx.search(lines[i + 1])
                if m2:
                    return m2.group(0), "next_line"

    # 2) fallback: scan whole document
    for ln in lines:
        m = rx.search(ln)
        if m:
            return m.group(0), "global_scan"

    return None, "not_found"

# ---------- vendor name (scored) ----------
def detect_vendor_name(lines, vendor_cfg, global_cfg):
    joined = " ".join(lines)
    names = vendor_cfg.get("vendor_keywords", {}).get("names", [])
    header_n = global_cfg["vendor_detection"]["header_zone_lines"]

    header_text = " ".join(lines[:max(1, header_n)])
    remit_text  = extract_remit_block(lines, global_cfg["vendor_detection"]["remit_headers"])

    def present(text, name): return name.lower() in (text or "").lower()

    scored = []
    for nm in names:
        score = 0
        if present(header_text, nm): score += 3
        if present(remit_text,  nm): score += 2
        if present(joined,      nm): score += 1
        if score > 0:
            scored.append((score, len(nm), nm))

    if scored:
        scored.sort(key=lambda t: (-t[0], -t[1]))
        return scored[0][2], "scored"

    guess = header_zone_guess(lines, header_n)
    if guess: return guess, "header_guess"

    return vendor_cfg.get("id", "global"), "fallback_id"

# ---------- invoice type (safer header keyword) ----------
def detect_invoice_type(lines, global_cfg):
    """
    Return a clean invoice type from the top of the page.
    Priority:
      1) exact multi-word phrases in include_keywords (e.g., RENTAL RETURN INVOICE)
      2) else, if a header line contains INVOICE, return exactly 'INVOICE'
    Lines like 'INVOICE NUMBER' are ignored.
    """
    it_cfg = global_cfg.get("invoice_type", {})
    n = it_cfg.get("header_zone_lines", 12)
    include = [s.upper() for s in it_cfg.get("include_keywords", ["INVOICE"])]
    exclude = [s.upper() for s in it_cfg.get("exclude_near", ["INVOICE NUMBER", "INVOICE #", "INVOICE NO"])]

    # Sort include phrases by length (longest first) so multi-word types win
    include_sorted = sorted(include, key=len, reverse=True)

    header_lines = lines[:max(1, n)]
    for ln in header_lines:
        up = re.sub(r"[^A-Z0-9/&\-\s]", " ", ln.upper())
        if any(ex in up for ex in exclude):
            continue
        # 1) exact phrases first (e.g., RENTAL RETURN INVOICE)
        for phrase in include_sorted:
            if phrase != "INVOICE" and phrase in up:
                return phrase
        # 2) fallback: plain INVOICE (stop at the word, ignore trailing text)
        if "INVOICE" in up:
            # ensure it's not part of 'INVOICE NUMBER', etc. (already excluded above)
            return "INVOICE"

    return None

# ---------- invoice total (simple, "amount"/"due"-first) ----------
def merge_total_field(vendor_cfg, global_cfg):
    """
    Pull invoice_total rules from vendor YAML if present; otherwise from global.yaml;
    otherwise use simple defaults. Uses 'ignore_words' (more human) in configs.
    """
    g = (global_cfg.get("fields", {}) or {}).get("invoice_total", {}) or {}
    v = (vendor_cfg.get("fields", {}) or {}).get("invoice_total", {}) or {}

    def pick(key, default):
        if key in ("keywords_must_include", "ignore_words"):
            src = v.get(key, g.get(key, default))
            return [w.lower() for w in src]
        return v.get(key, g.get(key, default))

    return {
        # we only care about lines that mention "amount" or "due"
        "keywords_must_include": pick("keywords_must_include", ["amount", "due"]),
        # words that look like totals but are not the final total line
        "ignore_words": pick("ignore_words", ["subtotal", "tax", "shipping", "handling", "freight"]),
        # money pattern
        "regex": pick("regex", r"\$?\s*-?\(?\d{1,3}(?:,\d{3})*(?:\.\d{2})?\)?"),
        # if value is on the line below the label
        "lookahead_lines": int(v.get("lookahead_lines", g.get("lookahead_lines", 2))),
    }

def _amount_to_number(txt: str) -> float:
    """'$1,234.50' or '(1,234.50)' -> 1234.50 (negative if parentheses)."""
    s = txt.strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("$", "").replace(",", "").replace("(", "").replace(")", "").strip()
    try:
        val = float(s)
        return -val if neg else val
    except Exception:
        return float("nan")

def pick_invoice_total(lines, keywords_must_include, ignore_words, regex, lookahead_lines=2):
    """
    Very simple rule:
      - Only consider lines that contain 'amount' or 'due' (case-insensitive).
      - Ignore lines that contain any word from ignore_words.
      - Grab the right-most amount on that line; if not found, check next lines.
      - Prefer the LAST matching line in the document; if tied, take the larger number.
    """
    rx = re.compile(regex)
    kws = [k.lower() for k in (keywords_must_include or [])]
    ignores = [w.lower() for w in (ignore_words or [])]

    def amounts_in(text):
        hits = rx.findall(text)
        return [(h if isinstance(h, str) else h[0]).strip() for h in hits]

    candidates = []  # (amount_text, numeric_value, line_index)

    for i, ln in enumerate(lines):
        low = ln.lower()
        if not any(k in low for k in kws):
            continue
        if any(w in low for w in ignores):
            continue

        # right-most amount on the same line
        amts = amounts_in(ln)
        if amts:
            candidates.append((amts[-1], _amount_to_number(amts[-1]), i))
            continue

        # try the next few lines if number is below the label
        for j in range(1, lookahead_lines + 1):
            if i + j < len(lines):
                nxt = lines[i + j]
                if any(w in nxt.lower() for w in ignores):
                    continue
                amts2 = amounts_in(nxt)
                if amts2:
                    candidates.append((amts2[-1], _amount_to_number(amts2[-1]), i + j))
                    break

    if not candidates:
        return None, "total_not_found"

    # Prefer the last occurrence; tie-break by larger numeric amount
    candidates.sort(key=lambda t: (t[2], t[1]))  # sort by (line_index, value)
    best = candidates[-1]
    return best[0], "amount_due_rule"

# ---------- load configs ----------
def load_vendor_cfgs():
    global_cfg = load_yaml(GLOBAL_CFG)
    vendor_cfgs = []
    for yml in CFG_DIR.glob("*.yaml"):
        if yml.name == "global.yaml":
            continue
        vc = load_yaml(yml)
        vc["id"] = vc.get("id", yml.stem)
        vendor_cfgs.append(vc)
    return global_cfg, vendor_cfgs

# ---------- per-PDF pipeline ----------
def process_pdf(pdf_path: Path):
    lines = read_pdf_lines(pdf_path)
    global_cfg, vendor_cfgs = load_vendor_cfgs()

    vendor_id, _ = detect_vendor_id(lines, vendor_cfgs, global_cfg)
    vcfg = next((v for v in vendor_cfgs if v["id"] == vendor_id),
                {"id": "global", "vendor_keywords": {"names": []}})

    inv_field = merge_invoice_field(vcfg, global_cfg)
    inv_num, _ = pick_invoice_number(lines, inv_field["anchors"], inv_field["regex"])
    vname, _   = detect_vendor_name(lines, vcfg, global_cfg)
    itype      = detect_invoice_type(lines, global_cfg)

    # Invoice total via simple "amount/due" rule
    tot_field = merge_total_field(vcfg, global_cfg)
    inv_total, _ = pick_invoice_total(
        lines,
        tot_field["keywords_must_include"],
        tot_field["ignore_words"],
        tot_field["regex"],
        lookahead_lines=tot_field.get("lookahead_lines", 2),
    )

    return {
        "pdf": str(pdf_path.name),
        "vendor_name": vname,
        "invoice_number": inv_num,
        "invoice_type": itype,
        "invoice_total": inv_total,
    }

# ---------- run & export ----------
if __name__ == "__main__":
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    results = [process_pdf(p) for p in sorted(SAMPLES_DIR.glob("*.pdf"))]

    rows = [{"Vendor Name": r.get("vendor_name"),
             "Invoice Number": r.get("invoice_number"),
             "Invoice Type": r.get("invoice_type"),
             "Invoice Total": r.get("invoice_total")}
            for r in results]

    df = pd.DataFrame(rows, columns=["Vendor Name", "Invoice Number", "Invoice Type", "Invoice Total"])
    out_xlsx = "extraction_results.xlsx"
    df.to_excel(out_xlsx, index=False)
    print(f"Saved {len(df)} rows to {out_xlsx}")
