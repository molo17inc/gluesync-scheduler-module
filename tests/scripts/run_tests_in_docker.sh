#!/bin/bash
# Script to run tests in Docker Compose environment

set -e  # Exit immediately if a command exits with a non-zero status

echo "Starting Docker Compose test environment..."
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit

# Clean up
echo "Cleaning up Docker Compose test environment..."
docker compose -f docker-compose.test.yml down

echo "Tests completed."
