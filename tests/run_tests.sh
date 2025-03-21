#!/bin/bash
# Script to run tests for the Gluesync Scheduler Module

set -e  # Exit immediately if a command exits with a non-zero status

# Create Gluesync default directories
mkdir -p /opt/gluesync/data

# Create license file for SDK from environment variable
if [ -z "$GLUESYNC_LICENSE_CONTENT" ]; then
    echo "Warning: GLUESYNC_LICENSE_CONTENT environment variable not set, using mock content"
    echo "mock-license-content" > /opt/gluesync/data/gs-license.dat
else
    echo "$GLUESYNC_LICENSE_CONTENT" > /opt/gluesync/data/gs-license.dat
fi

# Copy security config if it exists
if [ -f security-config.json ]; then
    cp security-config.json /opt/gluesync/data/security-config.json
fi

# Set up environment variables for testing
export GLUESYNC_LICENSE_FILE=/opt/gluesync/data/gs-license.dat
export GLUESYNC_MODULE_TAG=scheduler-module
export GLUESYNC_USE_SSL=False
export GLUESYNC_SECURITY_CONFIG=/opt/gluesync/data/security-config.json
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
