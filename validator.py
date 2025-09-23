def pick_invoice_total(
    lines,
    anchors,
    regex,
    discourage_words=None,
    lookahead_lines=2,
    due_keywords=None,
    net_keywords=None,
):
    """
    Pick the FINAL total (after tax/shipping), not earlier 'net' or 'subtotal'.

    How it decides:
      - Build candidates from:
          * anchor hits (same / next lines)
          * 'due' phrases (Total Due, Grand Total, Amount/Balance Due) -> strong boost
          * 'net' phrases (Net Invoice Amount, Invoice Amount) -> medium boost
      - Penalize lines that contain discourage words (subtotal, tax, shipping, etc.).
      - Prefer totals that appear AFTER nearby mentions of tax/shipping/fees.
      - Slightly prefer totals later in the document.
      - Tie-break by higher numeric amount.
    """
    rx = re.compile(regex)
    discourage_words = [w.lower() for w in (discourage_words or [])]
    due_keywords = [w.lower() for w in (due_keywords or [])]
    net_keywords = [w.lower() for w in (net_keywords or [])]

    def amounts_in(text):
        hits = rx.findall(text)
        return [(h if isinstance(h, str) else h[0]).strip() for h in hits]

    # Precompute where “adjusters” (tax/shipping/handling/etc.) appear
    adjuster_words = set(["tax", "sales tax", "shipping", "freight", "handling", "other charges", "misc"])
    adjuster_idxs = {i for i, ln in enumerate(lines) if any(w in ln.lower() for w in adjuster_words)}

    candidates = []  # (amount_text, value, line_idx, line_text, flags)

    # 1) Anchors -> same line and short lookahead
    for i, ln in enumerate(lines):
        low = ln.lower()
        if any(dw in low for dw in discourage_words):
            # this line is suspicious for final totals; skip as anchor host
            continue
        hits = [a for a in anchors if a.lower() in low]
        if not hits:
            continue

        a = min(hits, key=lambda x: low.find(x.lower()))
        cut = low.find(a.lower())
        segment = ln[cut + len(a):]
        amts = amounts_in(segment)
        if amts:
            candidates.append((amts[-1], _amount_to_number(amts[-1]), i, ln, {"anchor": True}))
        for j in range(1, lookahead_lines + 1):
            if i + j < len(lines):
                nxt = lines[i + j]
                if any(dw in nxt.lower() for dw in discourage_words):
                    continue
                amts2 = amounts_in(nxt)
                if amts2:
                    candidates.append((amts2[-1], _amount_to_number(amts2[-1]), i + j, nxt, {"anchor_next": j}))

    # 2) Total-ish lines (even without anchors)
    totalish = tuple(set(
        ["total", "total due", "amount due", "balance due", "grand total",
         "invoice amount", "net invoice amount"] + due_keywords + net_keywords
    ))
    for i, ln in enumerate(lines):
        low = ln.lower()
        if any(t in low for t in totalish):
            amts = amounts_in(ln)
            if amts:
                candidates.append((amts[-1], _amount_to_number(amts[-1]), i, ln, {"totalish": True}))

    if not candidates:
        # 3) Fallback: biggest amount anywhere
        all_amts = []
        for i, ln in enumerate(lines):
            for a in amounts_in(ln):
                all_amts.append((a, _amount_to_number(a), i, ln, {}))
        if not all_amts:
            return None, "total_not_found"
        best = max(all_amts, key=lambda t: t[1])
        return best[0], "total_max_in_doc"

    N = max(1, len(lines))

    def tax_context_bonus(idx: int) -> float:
        """
        If there are TAX/SHIPPING/… lines within the previous few lines, we boost,
        because final totals usually appear AFTER the adjustments.
        """
        window = range(max(0, idx - 8), idx + 1)
        return 3.0 if any(k in adjuster_idxs for k in window) else 0.0

    def score_item(item):
        amt_txt, val, idx, ln, flags = item
        low = ln.lower()
        s = 0.0

        # Strong positives
        if flags.get("anchor"):      s += 6.0
        if any(k in low for k in due_keywords): s += 7.0  # “Total Due / Grand Total / Amount Due / Balance Due”

        # Medium positives
        if flags.get("anchor_next"): s += 3.0
        if any(k in low for k in net_keywords): s += 3.0  # “Net Invoice Amount / Invoice Amount”

        # Tax/shipping context just above the line => favor this as *final total*
        s += tax_context_bonus(idx)

        # Negatives
        if any(dw in low for dw in discourage_words): s -= 6.0

        # Slight preference toward later-in-document totals
        s += 2.0 * (idx / N)

        return s

    # Choose by (score, value)
    best = max(candidates, key=lambda it: (score_item(it), it[1]))
    return best[0], "total_scored_final"
