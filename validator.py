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

  invoice_total:
    # Only consider lines that mention one of these words
    keywords_must_include:
      - "amount"
      - "due"
    # Lines that look like totals but are NOT the final total
    ignore_words:
      - "subtotal"
      - "sales tax"
      - "tax"
      - "shipping"
      - "freight"
      - "handling"
      - "other charges"
      - "misc"
    # Money like 1,234.56 or (1,234.56) or $1,234.56
    regex: "\\$?\\s*-?\\(?\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?\\)?"
    # If the number sits on the next line under the label
    lookahead_lines: 2

  invoice_date:
    labels:
      - "Invoice Date"
      - "Date"
      - "Inv Date"
      - "Billing Date"
      - "Bill Date"
    # MM/DD/YYYY, M/D/YY, Mon DD, YYYY, or 2025-09-23
    date_regex: "\\b(?:\\d{1,2}[/-]\\d{1,2}[/-]\\d{2,4}|[A-Za-z]{3,9}\\s+\\d{1,2},\\s*\\d{4}|20\\d{2}-\\d{2}-\\d{2})\\b"
    # Avoid dates that are ranges/periods or non-invoice contexts
    ignore_near:
      - "due"
      - "due by"
      - "ship"
      - "ship date"
      - "rental"
      - "service"
      - "from"
      - "thru"
      - "through"
      - "period"
    header_zone_lines: 30
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
