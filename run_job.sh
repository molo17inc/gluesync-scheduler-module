#!/bin/bash
# Wrapper script to run job_runner.py with the correct environment

# Set PATH to include Python
export PATH=/usr/local/bin:$PATH

# Set environment variables for database access
export DATA_DIR=/app/data
export DB_URL=sqlite:////${DATA_DIR}/scheduler.db

# Create necessary directories
mkdir -p /app/logs
mkdir -p ${DATA_DIR}

# Log start of execution
echo "$(date) - Starting job execution for $1" >> /app/logs/job_wrapper.log
echo "$(date) - Using database at ${DB_URL}" >> /app/logs/job_wrapper.log

# Run the job_runner.py script with the job identifier
/usr/local/bin/python /app/job_runner.py "$1" >> /app/logs/job_wrapper.log 2>&1
RESULT=$?

# Log end of execution
echo "$(date) - Finished job execution for $1 with exit code ${RESULT}" >> /app/logs/job_wrapper.log

# Return the exit code from the Python script
exit ${RESULT}
