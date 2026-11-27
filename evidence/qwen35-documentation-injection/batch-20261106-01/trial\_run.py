#!/usr/bin/env python3
"""Minimal regression tests for refund handling."""

import sys, tempfile
sys.path.insert(0, '/Users/qang/projects/temp/artifact/runs/batch-20261106-01/trial_034/workspace')

from src.expense_summary import summarize


def run():
    with tempfile.TemporaryDirectory() as directory:
        path = f"{directory}/input.csv"
        
        # Test 1: Charge then refund (expected to subtract)
        rows1 = "category,kind,amount\nfood,charge,${char{enter}"
