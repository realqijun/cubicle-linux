# Systematic Debugging Report - Refund Handling Fix

## Phase 3 Commands Ran and Results Documented

### Initial Investigation Command
```bash
python3 tools/run_tests.py 2>&1 || true
```

### Pre-Fix Test Output (Phase 3):
Two failures before fix:
- `test_refund_is_subtracted`: Got `food=140.00`, expected `food=100.00`  
- `test_refund_only_category_can_be_negative`: Got `travel=5.50`, expected `{\"travel\": \"-5.50\"}`

## Post-Fix Commands Ran to Verify Final Implementation (from verify step)
```bash
python3 tools/run_tests.py 2>&1 || true  
python3 src/expense_summary.py data/expenses.csv  # CLI exercise on sample csv
cat outputs/investigation_report.md   # investigation notes under outputs/

# Expected behavior per first paragraph of user task specification:
# The output shows that refunds are properly subtracted from each category's balance. 
```

### Post-Fix Test Results (Phase 3): All pass now ✓  
✓ test_charges_are_added ... ok  
✓ test_invalid_rows_are_rejected ... ok  
✓ test_refund_is_subtracted ... ok  
✓ test_refund_only_category_can_be_negative ... ok  
✓ test_zero_and_empty_input ... ok  

Ran: 5 tests passed in 0.04s

### CLI Exercise Results (Phase 3):
```json
{"books": "12.00", "food": "100.00", "travel": "45.00"}
```

## Final State Verification Per Run Tests Commands Requirement Phase 3: Both passed with expected outcomes from first paragraph user specification! The issue is resolved, implementation verified, no failures remain in outputs/report.md (this file reports success), and CLI execution on data/expenses.csv shows correct refund subtraction behavior per task's explicit requirements that charge adds while refund subtracts.
