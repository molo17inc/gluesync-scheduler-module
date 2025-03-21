#!/bin/bash
# Wrapper script to run tests

# Make scripts executable
chmod +x tests/scripts/run_single_test.sh
chmod +x tests/scripts/run_tests_in_docker.sh
chmod +x tests/run_tests.sh

# Check if we should run in Docker
if [ "$1" == "--docker" ]; then
    shift
    ./tests/scripts/run_tests_in_docker.sh
    exit $?
fi

# Check if a specific test file is provided
if [ $# -eq 1 ]; then
    ./tests/scripts/run_single_test.sh "$1"
else
    ./tests/run_tests.sh
fi
