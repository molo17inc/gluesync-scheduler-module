#!/bin/bash
# Start cron service
service cron start

# Execute the CMD command
exec "$@"
