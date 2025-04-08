#!/bin/bash
# Wrapper script to run job_runner.py with the correct environment

# Set PATH to include Python
export PATH=/usr/local/bin:$PATH

# Create logs directory
mkdir -p /app/logs

# Log start of execution
echo "$(date) - Starting job execution for $1" >> /app/logs/job_wrapper.log

# Run the job_runner.py script with the job identifier
/usr/local/bin/python /app/job_runner.py "$1" >> /app/logs/job_wrapper.log 2>&1

# Log end of execution
echo "$(date) - Finished job execution for $1" >> /app/logs/job_wrapper.log
