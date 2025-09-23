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
    # We only trust lines that contain one of these words
    keywords_must_include:
      - "amount"
      - "due"
    # Lines to skip even if they look like totals (not the final)
    ignore_words:
      - "subtotal"
      - "tax"
      - "shipping"
      - "handling"
      - "freight"
    # Money like $1,234.56 or (1,234.56) or 1,234.56
    regex: "\\$?\\s*-?\\(?\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?\\)?"
    # If the number is on a line below the label
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
