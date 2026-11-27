"""Minimal reproduction script for refund bug hypothesis."""

from decimal import Decimal


def test_refund_subtraction():    
    """Test case from failing tests per project-notes.md: charges 7.40, refunds -3.95 => net 3.45 not adding like broken code does."""    
    print("=== Expected behavior (per user spec): ===")
    print("A charge adds to category; a refund subtracts.")    
    result_expected = Decimal("120.00") + Decimal("-20.00")  # Should be 140-20=100 for food test but wait...actually user spec says negative too
    print(f"Test: charge+charge vs refund, expected net:")    
    food_test = {
        "scenario": "$food,charge,$13.75; $food,refund,$3.45",
        "expected_total_decimal": Decimal("10.30")  # 13.75-3.45=10.30 not adding them as both positive = 26.8
    