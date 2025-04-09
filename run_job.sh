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
if [ -f "${DB_FILE}" ]; then
    echo "Database file exists at ${DB_FILE}" >> /app/logs/job_wrapper.log
    echo "Database file size: $(ls -lh ${DB_FILE} | awk '{print $5}')" >> /app/logs/job_wrapper.log
    echo "Database file permissions: $(ls -la ${DB_FILE} | awk '{print $1}')" >> /app/logs/job_wrapper.log
    echo "Database file owner: $(ls -la ${DB_FILE} | awk '{print $3}')" >> /app/logs/job_wrapper.log
else
    echo "Database file does NOT exist at ${DB_FILE}" >> /app/logs/job_wrapper.log
    echo "Creating empty database directory" >> /app/logs/job_wrapper.log
    mkdir -p $(dirname ${DB_FILE})
    touch ${DB_FILE}
    echo "Created empty database file" >> /app/logs/job_wrapper.log
fi

# Run the job_runner.py script with the job identifier
echo "Executing: python /app/job_runner.py \"$1\"" >> /app/logs/job_wrapper.log
python /app/job_runner.py "$1" 2>&1 | tee -a /app/logs/job_wrapper.log
RESULT=${PIPESTATUS[0]}

# Log end of execution
echo "$(date) - Finished job execution for $1 with exit code ${RESULT}" >> /app/logs/job_wrapper.log
echo "===== JOB EXECUTION END =====" >> /app/logs/job_wrapper.log
echo "" >> /app/logs/job_wrapper.log

# Return the exit code from the Python script
exit ${RESULT}
