#!/bin/bash
# Script to run tests for the Gluesync Scheduler Module (aka Chronos)

set -e # Exit immediately if a command exits with a non-zero status

# Initialize and update the Git submodule
git submodule init
git submodule update

# Create test data directory
mkdir -p ./tests/data

# Create local Gluesync directories for testing
mkdir -p ./tests/gluesync/data

# Create license file for SDK from environment variable
if [ -z "$GLUESYNC_LICENSE_CONTENT" ]; then
    echo "Warning: GLUESYNC_LICENSE_CONTENT environment variable not set, using mock content"
    echo "mock-license-content" >./tests/gluesync/data/gs-license.dat
else
    echo "$GLUESYNC_LICENSE_CONTENT" >./tests/gluesync/data/gs-license.dat
fi

# Copy security config if it exists
if [ -f security-config.json ]; then
    cp security-config.json ./tests/gluesync/data/security-config.json
else
    # Create a default empty security config
    echo '{}' >./tests/gluesync/data/security-config.json
fi

# Set up environment variables for testing
export GLUESYNC_LICENSE_FILE=./tests/gluesync/data/gs-license.dat
export GLUESYNC_MODULE_TAG=chronos
export SSL_ENABLED=False
export GLUESYNC_SECURITY_CONFIG=./tests/gluesync/data/security-config.json
export DB_URL=sqlite:///./tests/data/test_scheduler.db
export DEBUG=True
export CORE_HUB_URL=http://localhost:8080
export CRONTAB_USER=$USER
export HOST=0.0.0.0 # Bind to all interfaces

# Install specific websockets version first to avoid compatibility issues
python3 -m pip install websockets==11.0.3 --break-system-packages

# Check if we should use the mock implementation
USE_MOCK=${USE_MOCK:-true}

# Install the SDK from the submodule or use mock
if [ "$USE_MOCK" = "false" ] && [ -d "./gluesync-sdk" ]; then
    echo "Installing real gluesync-sdk from submodule..."
    python3 -m pip install ./gluesync-sdk --break-system-packages
    echo "Using real gluesync-sdk implementation"
else
    echo "Using mock gluesync-sdk implementation"
    # Make sure the mock files are executable
    chmod +x tests/run_with_mock.py
fi

# Check if a specific test file is provided
if [ $# -eq 1 ]; then
    TEST_PATH=$1
else
    TEST_PATH="tests/"
fi

# Install pytest and other testing dependencies
python3 -m pip install pytest pytest-cov --break-system-packages

# Run the tests with coverage
echo "Running tests in $TEST_PATH"
python3 -m pytest $TEST_PATH -v --cov=. --cov-report=term --cov-report=html:coverage-report --junitxml=test-results.xml

# Print summary
echo "Tests completed. Coverage report is available in coverage-report/"
