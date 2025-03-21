#!/bin/bash
# Script to run tests for the Gluesync Scheduler Module

set -e  # Exit immediately if a command exits with a non-zero status

# Create license file for SDK from environment variable
if [ -z "$GLUESYNC_LICENSE_CONTENT" ]; then
    echo "Warning: GLUESYNC_LICENSE_CONTENT environment variable not set, using mock content"
    echo "mock-license-content" > gs-license.dat
else
    echo "$GLUESYNC_LICENSE_CONTENT" > gs-license.dat
fi

# Set up environment variables for testing
export GLUESYNC_LICENSE_FILE=gs-license.dat
export GLUESYNC_MODULE_TAG=scheduler-module
export GLUESYNC_USE_SSL=False
export DB_URL=sqlite:///./tests/data/test_scheduler.db
export DEBUG=True
export CORE_HUB_URL=http://localhost:8080
export CRONTAB_USER=$USER

# Check if a specific test file is provided
if [ $# -eq 1 ]; then
    TEST_PATH=$1
else
    TEST_PATH="tests/"
fi

# Run the tests with coverage
echo "Running tests in $TEST_PATH"
python3 -m pytest $TEST_PATH -v --cov=. --cov-report=term --cov-report=html:coverage-report --junitxml=test-results.xml

# Print summary
echo "Tests completed. Coverage report is available in coverage-report/"
