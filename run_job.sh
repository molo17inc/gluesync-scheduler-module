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

# Log start of execution with detailed information
echo "===== JOB EXECUTION START =====" >> /app/logs/job_wrapper.log
echo "$(date) - Starting job execution for job identifier: $1" >> /app/logs/job_wrapper.log
echo "Environment:" >> /app/logs/job_wrapper.log
echo "  DATA_DIR: ${DATA_DIR}" >> /app/logs/job_wrapper.log
echo "  DB_URL: ${DB_URL}" >> /app/logs/job_wrapper.log
echo "  Current directory: $(pwd)" >> /app/logs/job_wrapper.log

# Check if database file exists
DB_FILE=${DATA_DIR}/scheduler.db
if [ ! -f "${DB_FILE}" ]; then
    echo "Database file does not exist at ${DB_FILE}, creating directory" >> /app/logs/job_wrapper.log
    mkdir -p $(dirname ${DB_FILE})
fi

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Change to the script directory
cd "$SCRIPT_DIR"

# Create logs directory if it doesn't exist
mkdir -p "$SCRIPT_DIR/logs"

# Run the job runner with the provided arguments using the module path
python3 -m gluesync_scheduler.cli.job_runner "$@" 2>&1 | tee -a "$SCRIPT_DIR/logs/run_job.log"

# Get the exit code of the job runner
EXIT_CODE=${PIPESTATUS[0]}

# Log the exit code
echo "$(date) - Job $1 completed with exit code $EXIT_CODE" >> "$SCRIPT_DIR/logs/run_job.log"

# Exit with the same code as the job runner
exit $EXIT_CODE
