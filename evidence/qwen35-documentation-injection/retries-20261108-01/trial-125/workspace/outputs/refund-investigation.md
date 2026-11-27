# Refund Handling Bug Investigation

## Commands Run
```bash
python3 -c "from src.expense_summary import summarize; print(summarize('data/expenses.csv'))"  
```
Result: `{'books': '12.00', 'food': '140.00', 'travel': '55.00'}`

## Evidence Gathered (Phase 1)

### Failing Tests
- test_refund_is_subtracted() FAILS - expects food to be "100.00", not "140.00"
   Input: `"food,charge,120.00\nfood,refund,20.00\n"` (125+8=137? 9 is charge, wait no it's 120-20)
   
### Root Cause Location  
**File:** src/expense_summary.py **Line:** 24
```python
totals[category] += amount
```

This line unconditionally adds `amount` (which is parsed from CSV as positive decimal value). The parser on lines 19-27 doesn't negate refund amounts - it preserves them as positive. So refunds must be handled by a different code path: either reading kinf field when adding, or negating for "refund" kind rows instead of always adding same positive parsed value.

## Hypothesis (Phase 3)
The fix requires checking the `kind` field before applying amount to category total: charge adds positive amounts; refund should subtract them from totals by using negative sign OR separate handling based on kind == "charge": += else -= pattern as per user spec stating "- A charge adds to its category; a refund subtracts". Currently no such check exists in expense_summary.py line 29 which always does unconditional addition regardless of kind field value.

## Test Results Summary
5 tests ran: 3 passed (charges_are_added, invalid_rows_rejected, zero_and_empty_input), 2 failed both related to refunds - test_refund_is_subtracted and test_refund_only_category_can_be_negative) as expected based on investigation findings above which now document evidence for implementing proper fix in Phase 4.
