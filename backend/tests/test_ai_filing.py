from instilens.ai.filing_extract import TxExtraction, TxRow, validate


def test_validate_drops_numbers_not_in_text():
    text = "11.09.2026 tarihinde 3.027.970 adet satış işlemi sonucunda oranı %3.14'den %2,84'e düşmüştür."
    ex = TxExtraction(rows=[
        TxRow(transaction_date="11.09.2026", side="SATIS", nominal=3027970, ownership_before_pct="3.14", ownership_after_pct="2.84"),
        TxRow(transaction_date="11.09.2026", side="ALIS", nominal=999999),      # hallucinated: not in text
        TxRow(transaction_date="31.02.2026", side="ALIS", nominal=3027970),     # bad date
    ])
    rows = validate(ex, text)
    assert rows == [{"transaction_date": "2026-09-11", "side": "SATIS", "nominal": 3027970, "price": None, "before": "3.14", "after": "2.84"}]
