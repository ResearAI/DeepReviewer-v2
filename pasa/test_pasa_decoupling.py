#!/usr/bin/env python3
"""
Test script for PASA decoupling architecture
============================================
This script tests the decoupled PASA architecture by:
1. Checking if PASA server is running
2. Testing the health endpoint
3. Testing a simple search request

Usage:
    python3 test_pasa_decoupling.py
"""

import sys
import json
import requests
from typing import Dict, Any

# This is an executable integration script, not a pytest test module.
__test__ = False

# ANSI color codes for output
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'  # No Color


def print_header(text: str):
    """Print a formatted header."""
    print(f"\n{BLUE}{'=' * 70}{NC}")
    print(f"{BLUE}  {text}{NC}")
    print(f"{BLUE}{'=' * 70}{NC}\n")


def print_success(text: str):
    """Print a success message."""
    print(f"{GREEN}✅ {text}{NC}")


def print_error(text: str):
    """Print an error message."""
    print(f"{RED}❌ {text}{NC}")


def print_warning(text: str):
    """Print a warning message."""
    print(f"{YELLOW}⚠️  {text}{NC}")


def print_info(text: str):
    """Print an info message."""
    print(f"{BLUE}ℹ️  {text}{NC}")


def test_server_connectivity(base_url: str) -> bool:
    """Test if the server is reachable."""
    print_header("Test 1: Server Connectivity")

    try:
        response = requests.get(f"{base_url}/", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print_success(f"Server is reachable at {base_url}")
            print_info(f"Service: {data.get('service', 'N/A')}")
            print_info(f"Version: {data.get('version', 'N/A')}")
            return True
        else:
            print_error(f"Server returned status code: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print_error(f"Failed to connect to server: {e}")
        print_info("Make sure the PASA server is running:")
        print_info("  bash pasa/start_pasa_server.sh")
        return False


def test_health_endpoint(base_url: str) -> bool:
    """Test the health endpoint and check if models are loaded."""
    print_header("Test 2: Health Endpoint")

    try:
        response = requests.get(f"{base_url}/health", timeout=10)
        data = response.json()

        # Print health information
        status = data.get("status", "unknown")
        if status == "healthy":
            print_success(f"Server status: {status}")
        else:
            print_error(f"Server status: {status}")

        print_info(f"Models loaded: {data.get('models_loaded', False)}")
        print_info(f"Backend: {data.get('backend', 'N/A')}")
        print_info(f"Crawler ready: {data.get('crawler_ready', False)}")
        print_info(f"Selector ready: {data.get('selector_ready', False)}")
        print_info(f"GPU: {data.get('gpu', 'N/A')}")

        vllm_cfg = data.get("vllm") or {}
        if vllm_cfg:
            print_info(f"vLLM crawler: {vllm_cfg.get('crawler_url', 'N/A')} ({vllm_cfg.get('crawler_model_name', 'N/A')})")
            print_info(f"vLLM selector: {vllm_cfg.get('selector_url', 'N/A')} ({vllm_cfg.get('selector_model_name', 'N/A')})")

        if data.get('error'):
            print_error(f"Server error: {data['error']}")

        # Check if models are loaded
        if data.get('models_loaded', False):
            print_success("All models are loaded and ready")
            return True
        else:
            print_error("Models are not loaded")
            print_warning("The server may still be loading models (this can take 1-5 minutes)")
            return False

    except requests.exceptions.RequestException as e:
        print_error(f"Failed to check health: {e}")
        return False


def test_search_endpoint(base_url: str) -> bool:
    """Test the search endpoint with a simple query."""
    print_header("Test 3: Search Endpoint")

    # Simple test query
    test_query = "Papers about contrastive learning"
    request_data = {
        "query": test_query,
        "expand_layers": 1,      # Reduced for faster testing
        "search_queries": 2,     # Reduced for faster testing
        "search_papers": 5,      # Reduced for faster testing
        "expand_papers": 5,      # Reduced for faster testing
        "threads_num": 0
    }

    print_info(f"Test query: {test_query}")
    print_info("This may take 30-120 seconds...")

    try:
        response = requests.post(
            f"{base_url}/pasa/search",
            json=request_data,
            timeout=300  # 5 minutes timeout
        )

        if response.status_code == 200:
            results = response.json()
            print_success(f"Search completed successfully")
            print_info(f"Found {len(results)} papers")

            # Display first result as sample
            if results:
                print_info("\nSample result:")
                print_info(f"  Title: {results[0].get('title', 'N/A')[:80]}...")
                print_info(f"  Link: {results[0].get('link', 'N/A')}")
                print_info(f"  Snippet: {results[0].get('snippet', 'N/A')[:100]}...")

            return True
        else:
            print_error(f"Search failed with status code: {response.status_code}")
            print_error(f"Response: {response.text}")
            return False

    except requests.exceptions.Timeout:
        print_error("Search request timed out (exceeded 5 minutes)")
        print_warning("The query may be too complex or the server may be overloaded")
        return False
    except requests.exceptions.RequestException as e:
        print_error(f"Search request failed: {e}")
        return False


def main():
    """Run all tests."""
    print_header("PASA Decoupling Architecture Test Suite")

    # Configuration
    base_url = "http://localhost:8001"
    print_info(f"Testing PASA server at: {base_url}")
    print_info("Make sure the PASA server is running before running this test")
    print()

    # Run tests
    results = []

    # Test 1: Connectivity
    results.append(("Connectivity", test_server_connectivity(base_url)))

    if not results[-1][1]:
        print_error("\nServer is not reachable. Cannot proceed with other tests.")
        print_info("\nTo start the PASA server:")
        print_info("  cd <repo_root>/pasa")
        print_info("  bash start_pasa_server.sh --background")
        sys.exit(1)

    # Test 2: Health
    results.append(("Health Check", test_health_endpoint(base_url)))

    if not results[-1][1]:
        print_warning("\nModels are not loaded. Skipping search test.")
        print_info("Wait for models to load and run this test again")
    else:
        # Test 3: Search (only if health check passed)
        results.append(("Search Function", test_search_endpoint(base_url)))

    # Summary
    print_header("Test Summary")

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = f"{GREEN}PASSED{NC}" if result else f"{RED}FAILED{NC}"
        print(f"  {test_name}: {status}")

    print()
    print(f"Total: {passed}/{total} tests passed")

    if passed == total:
        print_success("\nAll tests passed! PASA decoupling architecture is working correctly.")
        sys.exit(0)
    else:
        print_error(f"\n{total - passed} test(s) failed. Please check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
