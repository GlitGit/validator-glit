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

  # Invoice total defaults used by all vendors unless overridden
  invoice_total:
    anchors:
      - "Invoice Amount"
      - "Total Due"
      - "Net Invoice Amount"
      - "Grand Total"
    regex: "\\$?\\s*-?\\(?\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?\\)?"
    # things that look like totals but aren’t the final bill
    discourage:
      - "subtotal"
      - "tax"
      - "shipping"
      - "handling"
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
