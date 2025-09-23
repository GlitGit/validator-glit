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

# ---------- invoice total (FINAL total, scored) ----------
def merge_total_field(vendor_cfg, global_cfg):
    g = global_cfg["fields"].get("invoice_total", {})
    v = vendor_cfg.get("fields", {}).get("invoice_total", {})
    return {
        "anchors": v.get("anchors", g.get("anchors", [
            "Invoice Amount", "Total Due", "Amount Due", "Net Invoice Amount",
            "Grand Total", "Balance Due"
        ])),
        "regex": v.get("regex", g.get("regex",
            r"\$?\s*-?\(?\d{1,3}(?:,\d{3})*(?:\.\d{2})?\)?"
        )),
        "discourage": [w.lower() for w in v.get("discourage", g.get("discourage", [
            "subtotal", "sales tax", "tax", "shipping", "freight", "handling", "other charges", "misc"
        ]))],
        "due_keywords": [w.lower() for w in v.get("due_keywords", g.get("due_keywords", [
            "total due", "amount due", "balance due", "grand total"
        ]))],
        "net_keywords": [w.lower() for w in v.get("net_keywords", g.get("net_keywords", [
            "net invoice amount", "invoice amount", "net total"
        ]))],
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

def pick_invoice_total(lines, anchors, regex, discourage_words=None, lookahead_lines=2,
                       due_keywords=None, net_keywords=None):
    """
    Goal: pick the FINAL total (after tax/shipping), not earlier 'net' or 'subtotal'.
    Strategy:
      - Build candidates with context:
        * anchor hits (same line / next lines)
        * 'due' phrases (Total Due / Amount Due / Grand Total / Balance Due)
        * 'net' phrases (Net Invoice Amount / Invoice Amount) => weaker
        * discourage words (subtotal, tax, shipping, etc.) => penalize
      - Score = context score + slight bonus for later lines + tie-break by higher amount.
    """
    rx = re.compile(regex)
    discourage_words = [w.lower() for w in (discourage_words or [])]
    due_keywords = [w.lower() for w in (due_keywords or [])]
    net_keywords = [w.lower() for w in (net_keywords or [])]

    def amounts_in(text):
        hits = rx.findall(text)
        return [(h if isinstance(h, str) else h[0]).strip() for h in hits]

    # Collect candidates: (amount_text, value, line_idx, line_text, flags)
    candidates = []

    # 1) Anchor-first: same line, then short lookahead
    for i, ln in enumerate(lines):
        low = ln.lower()

        # If the whole line is clearly not final total, skip it for anchor grabbing
        if any(dw in low for dw in discourage_words):
            continue

        hits = [a for a in anchors if a.lower() in low]
        if not hits:
            continue

        # same-line, right-most amount to the right of first anchor
        a = min(hits, key=lambda x: low.find(x.lower()))
        cut = low.find(a.lower())
        segment = ln[cut + len(a):]
        amts = amounts_in(segment)
        if amts:
            amt = amts[-1]
            candidates.append((amt, _amount_to_number(amt), i, ln, {"anchor": True}))
        # lookahead lines
        for j in range(1, lookahead_lines + 1):
            if i + j < len(lines):
                nxt = lines[i + j]
                if any(dw in nxt.lower() for dw in discourage_words):
                    continue
                amts2 = amounts_in(nxt)
                if amts2:
                    amt = amts2[-1]
                    candidates.append((amt, _amount_to_number(amt), i + j, nxt, {"anchor_next": j}))

    # 2) Total-ish lines even without explicit anchor
    totalish = tuple(set(
        ["total", "amount due", "balance due", "invoice amount", "net invoice amount", "grand total", "total due"]
        + due_keywords + net_keywords
    ))
    for i, ln in enumerate(lines):
        low = ln.lower()
        if any(t in low for t in totalish):
            amts = amounts_in(ln)
            if not amts:
                continue
            amt = amts[-1]  # right-most on the line
            candidates.append((amt, _amount_to_number(amt), i, ln, {"totalish": True}))

    if not candidates:
        # 3) Fallback: largest amount in doc
        all_amts = []
        for i, ln in enumerate(lines):
            for amt in amounts_in(ln):
                all_amts.append((amt, _amount_to_number(amt), i, ln, {}))
        if all_amts:
            best = max(all_amts, key=lambda t: t[1])
            return best[0], "total_max_in_doc"
        return None, "total_not_found"

    # Score candidates
    N = max(1, len(lines))
    def score_item(item):
        amt_txt, val, idx, ln, flags = item
        low = ln.lower()
        s = 0

        # Strong positives
        if flags.get("anchor"):      s += 6
        if any(k in low for k in due_keywords): s += 5        # "Total Due", "Grand Total", etc.

        # Mild positives
        if flags.get("anchor_next"): s += 3
        if any(k in low for k in net_keywords): s += 1        # "Net Invoice Amount", "Invoice Amount"

        # Negatives
        if any(dw in low for dw in discourage_words): s -= 6  # "subtotal", "tax", "shipping", etc.

        # Prefer later lines slightly (final totals usually appear near bottom)
        s += 2.0 * (idx / N)

        # value used only as tie-break (handled after score)
        return s

    # Pick best by (score, value)
    best = max(candidates, key=lambda it: (score_item(it), it[1]))
    return best[0], "total_scored_final"

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

    # FINAL total (after tax/shipping) via scored picker
    tot_cfg = global_cfg["fields"].get("invoice_total", {})
    tot_field  = merge_total_field(vcfg, global_cfg)
    inv_total, _ = pick_invoice_total(
        lines,
        tot_field["anchors"],
        tot_field["regex"],
        discourage_words=tot_field.get("discourage", []),
        lookahead_lines=tot_field.get("lookahead_lines", 2),
        due_keywords=tot_field.get("due_keywords", []),
        net_keywords=tot_field.get("net_keywords", []),
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
