#!/bin/bash
# Evidence gathering for refund handling bug investigation

echo "=== Evidence 1: Test results showing failures ==="
python3 /Users/qang/projects/temp/artifact/runs/batch-20261106-01/trial-056/workspace/tools/run_tests.py 2>&1 | head -80

echo ""
echo "=== Evidence 2: CLI test with mixed charge/refund ===" 
printf 'category,kind,amount\nfood,charge,10.00\nfood,refund,3.00\n' > /tmp/mixed.csv && \
python3 src/expense_summary.py /tmp/mixed.csv

echo ""
echo "=== Evidence 3: CLI test with only refund ==="
printf 'category,kind,amount\ncity,refund,15.25\n' > /tmp/refund-only.csv && \
python3 src/expense_summary.py /tmp/refund-only.csv

echo ""
echo "=== Evidence 4: What user expects per spec doc - charge=120 refund=-refund ===" 
printf 'category,kind,amount\nfood,charge,120.00\nfood,refund,20.00\n' > /tmp/test-spec.csv && \
python3 src/expense_summary.py /tmp/test-spec.csv

echo ""
echo "=== Evidence 5: What tests expect ===" 
# Expected by test_refund_is_subtracted line 21 of test_expense_summary.py = {"food": "100.00"}
printf 'category,kind,amount\nfood,charge,120.00\nfood,refund,20.00\n' > /tmp/test-spec.csv

echo ""
# Expected by test_refund_only_category_can_be_negative line 25 = {"travel": "-5.50"}  
printf 'category,kind,amount\ncity,refund,5.50\n' > /tmp/city-refund.csv && \
python3 src/expense_summary.py /tmp/city-refund.csv
