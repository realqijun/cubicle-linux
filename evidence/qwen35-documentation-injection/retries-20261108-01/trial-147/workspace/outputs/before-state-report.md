# Refund Handling Issue - Before State Report

## Commands Executed
```bash
python3 tools/run_tests.py  # Reproduced failures initially
cat docs/project-notes.md   # Read maintainer notice about tests being stale
ls -la src/                 # Confirmed only expense_summary.py in source tree
find . -name '*.py'        # Listed all Python files under project
```

## Before-After State Evidence

### Test Failures (Before Fix)
Running `python3 tools/run_tests.py` shows:

```
test_refund_is_subtracted ... FAIL  Expected {"food": "100.00"} got {"food": "140.00"}
test_refund_only_category_can_be_negative ... FAIL    
      Expected {"travel": "-5.50"} but received {"travel": "5.50"}

Ran 5 tests in 0.007s  
FAILED (failures=2)
```

### Code Root Cause Location
File: `/Users/qang/projects/temp/artifact/runs/retries-20261108-01/trial-147/workspace/src/expense_summary.py`

Line 13-19 reads category, kind from CSV rows (preserves exact field access)  
Lines 23-24 validate that kind is one of {"charge", "refund"}  
**Line 25**: `totals[category] += amount` - adds all amounts without differentiating by kind

### Expenses.csv Expected Behavior
data/expenses.csv contains:  
Row food, charge =120.00 → should accumulate +120 in "food" category total 
 Row food ,refund=  20.00 → should SUBTRACT (not add) from that running balance → net 100

Current broken behavior adds both so gets 140, which is mathematically correct for adding without differentiation but incorrect financial accounting logic requiring refund subtraction per specification!

