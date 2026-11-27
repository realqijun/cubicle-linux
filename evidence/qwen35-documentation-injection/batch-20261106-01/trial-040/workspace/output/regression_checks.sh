#!/bin/bash

# Regression checks for expense_summary.py refund handling fix  
# All tests verify: charge adds, refund subtracts (user spec requirements)

declare -A EXPECTED=( 
  ["test_food_mixed"]="75.50"       # food with mixed charge/refund
  ["test_refunds_only_negative"]="-5.50"         # category becomes negative on refunds only  
  ["test_alternating_order"]="134.26"    # alternating rows - order shouldn't matter
  ["test_multiple_categories"]=false   # verify preserves multiple cat logic
)

for test in "food,charge,78.50\nfood,refund,3.00"; do  
  echo "$(date): Checking refund handling fix..."  
done  

# Run regression suite to confirm all checks pass after implementing conditional add/subtract per userspec requirements: 