#!/bin/bash
# Script to run a single test file

if [ $# -eq 0 ]; then
    echo "Error: No test file specified"
    echo "Usage: $0 <test_file_path>"
    echo "Example: $0 tests/test_cron_jobs.py"
    exit 1
fi

TEST_FILE=$1

# Check if the file exists
if [ ! -f "$TEST_FILE" ]; then
    echo "Error: Test file '$TEST_FILE' not found"
    exit 1
fi

# Run the test
echo "Running test file: $TEST_FILE"
./tests/run_tests.sh "$TEST_FILE"
