#!/bin/bash
# Regression checks for expense summary compatibility fix

SCRIPT_DIR="$(dirname "$0")"
OUTPUTS="$SCRIPT_DIR/outputs/"

echo "=== Regression Check 1: Unit tests ===" 
python3 tools/run_tests.py > /tmp/regression-checks.log 2>&1 || exit $?

grep "^Ran.*tests in" /tmp/regression-checks.log
if grep -q "^OK$" /tmp/regression-checks.log; then echo "[PASSED] All unit tests"; else echo "[FAILED] Some tests failed" && cat /tmp/regression-checks.log; fi

echo "=== Regression Check 2: CLI on sample data ===" 
INPUT=$(cat << 'CSV'
category,kind,amount
food,charge,10.00
food,refund,3.00
travel,charge,50.00
travel,refund,49.80
travel,pending,0.20

books,charge,12.60
CSV
)
echo "$INPUT" > $OUTPUTS/clinetest.csv

python3 src/expense_summary.py data/expenses.csv 2>&1 | head -5 || python3 src/expense_summary.py $OUTPUTS/cli-input-regression-check.csv 
