#!/bin/bash
set -e

# Display environment variables for debugging
echo "Environment variables at startup:"
echo "TZ=${TZ}"

# Set the timezone in the container (this affects Python's time functions)
if [ -n "$TZ" ]; then
  echo "Setting timezone to $TZ"
  # Also set it for the container's system time
  ln -snf /usr/share/zoneinfo/$TZ /etc/localtime
  echo "$TZ" > /etc/timezone
fi

# Start the application
exec "$@"
