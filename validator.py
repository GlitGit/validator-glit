fields:
  invoice_number:
    anchors:
      - "Invoice Number"
      - "Invoice #"
      - "Inv No"
      - "Doc No"
      - "Document No"
      - "Invoice"
    regex: "\\b[A-Za-z]{0,4}[- ]?\\d{4,}\\b"

  # Final invoice total defaults (works for most vendors; vendor YAML can override)
  invoice_total:
    anchors:
      - "Invoice Amount"
      - "Total Due"
      - "Amount Due"
      - "Net Invoice Amount"
      - "Grand Total"
      - "Balance Due"
    # money like 1,234.56 or (1,234.56) or $1,234.56
    regex: "\\$?\\s*-?\\(?\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?\\)?"

    # lines we should avoid treating as the final total
    discourage:
      - "subtotal"
      - "sales tax"
      - "tax"
      - "shipping"
      - "freight"
      - "handling"
      - "other charges"
      - "misc"

    # phrases that indicate FINAL-ish totals (bigger boost than anchors)
    due_keywords:
      - "total due"
      - "amount due"
      - "balance due"
      - "grand total"

    # phrases that are totals but often BEFORE tax/shipping (smaller boost)
    net_keywords:
      - "net invoice amount"
      - "invoice amount"
      - "net total"

    # how many lines to look after an anchor for an amount
    lookahead_lines: 2

vendor_detection:
  remit_headers:
    - "Remit To"
    - "Remittance"
    - "Pay To"
    - "Payment Remit To"
  header_zone_lines: 8

invoice_type:
  header_zone_lines: 12
  include_keywords:
    - "RENTAL RETURN INVOICE"
    - "CREDIT MEMO"
    - "DEBIT MEMO"
    - "STATEMENT"
    - "INVOICE"
  exclude_near:
    - "INVOICE NUMBER"
    - "INVOICE #"
    - "INVOICE NO"
