#!/usr/bin/env python3
"""
Simple script to test if cron is working properly in the Docker environment.
"""

import os
import sys
import datetime

# Create test directory
test_dir = "/app/logs"
os.makedirs(test_dir, exist_ok=True)

# Get the current timestamp
timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# Write to a test file
with open(f"{test_dir}/cron_test.log", "a") as f:
    f.write(f"{timestamp} - Cron test executed with args: {sys.argv}\n")

# Also write to stdout for debugging
print(f"{timestamp} - Cron test executed")

# Exit with success
sys.exit(0)
