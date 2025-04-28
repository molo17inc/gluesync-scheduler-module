#!/bin/bash
set -e

# Display environment variables for debugging
echo "Environment variables at startup:"
echo "TIMEZONE=${TIMEZONE}"
echo "TZ=${TZ}"

# Set the timezone in the container (this affects Python's time functions)
if [ -n "$TIMEZONE" ]; then
  echo "Setting timezone to $TIMEZONE"
  # Set the TZ environment variable which is used by Python's time functions
  export TZ="$TIMEZONE"
  # Also set it for the container's system time
  ln -snf /usr/share/zoneinfo/$TIMEZONE /etc/localtime
  echo "$TIMEZONE" > /etc/timezone
fi

# Start the application
exec "$@"
