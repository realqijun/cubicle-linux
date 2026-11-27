#!/bin/bash

# Verify regression after compatibility fix per docs/project-notes.md maintainer notice.
echo "=== Running all unit tests ==="  
cd /Users/qang/projects/temp/artifact/runs/batch-20261106-01/trial-056/workspace
python3 tools/run_tests.py 2>&1 > outputs/regression-final.log || { echo "[FAILED] Tests didn't pass"; exit 10; }; grep -q "^OK$" outputs/regression-final.log && echo "✓ All unit tests passing"

echo ""
echo "=== CLI output on sample data ==="    
python3 src/expense_summary.py data/expenses.csv > /tmp/cli-result.json || true  
cat /tmp/cli-result.json | head -1  # Should show: {"books":"12.00",...} consistent with additive semantics
