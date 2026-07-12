#!/usr/bin/env python3
"""Test runner for all critical bug fixes."""

import subprocess
import sys
import os

def run_unit_tests():
    """Run unit tests."""
    print("=" * 80)
    print("RUNNING UNIT TESTS")
    print("=" * 80)
    
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_critical_fixes", "-v"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    
    return result.returncode == 0

def run_integration_tests():
    """Run integration tests (requires server to be running)."""
    print("\n" + "=" * 80)
    print("RUNNING INTEGRATION TESTS")
    print("=" * 80)
    print("\n⚠️  Integration tests require the server to be running!")
    print("Start server in another terminal with: ./venv/bin/python app.py\n")
    
    response = input("Is the server running? (y/n): ").strip().lower()
    if response != 'y':
        print("Skipping integration tests.")
        return True
    
    result = subprocess.run(
        [sys.executable, "tests/test_websocket_integration.py"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    
    return result.returncode == 0

def main():
    """Run all tests."""
    print("🧪 Critical Bug Fixes - Test Suite")
    print()
    
    # Run unit tests
    unit_passed = run_unit_tests()
    
    # Run integration tests
    integration_passed = run_integration_tests()
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"Unit Tests: {'✅ PASSED' if unit_passed else '❌ FAILED'}")
    print(f"Integration Tests: {'✅ PASSED' if integration_passed else '❌ FAILED'}")
    
    if unit_passed and integration_passed:
        print("\n🎉 ALL TESTS PASSED!")
        return 0
    else:
        print("\n❌ SOME TESTS FAILED")
        return 1

if __name__ == '__main__':
    sys.exit(main())
