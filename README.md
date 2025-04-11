# Gluesync Scheduler Module (aka Chronos)

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![License](https://img.shields.io/badge/license-Dual-green)

A backend service that provides a set of REST APIs for scheduling and managing cron jobs to automate Gluesync tasks, including starting/stopping entities and pipelines, as well as running snapshots.

## Features

- **Comprehensive REST API**: Full CRUD operations for scheduled jobs
- **Flexible Job Scheduling**: Choose between user-friendly schedule format or standard cron expressions
- **Multiple Task Types**:
  - Start/stop entities
  - Start/stop entire pipelines
  - Schedule snapshots for entities
  - More task types can be easily added
- **Job Management**: View, create, update, disable/enable, and delete scheduled jobs
- **Containerized Deployment**: Docker support for easy deployment
- **CoreHub Integration**: Direct SDK integration with Gluesync CoreHub for secure authentication and communication

## Prerequisites

- Python 3.8+
- Access to crontab (for Unix-based systems)
- Gluesync Core Hub instance

## Installation

### Local Development

1. Clone the repository:

   ```bash
   git clone https://gitlab.com/molo17-public/gluesync/gluesync-scheduler-module.git
   cd gluesync-scheduler-module
   ```

2. Create a virtual environment and activate it:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Initialize the database:

   ```bash
   python init_db.py
   ```

5. Run the application:

   ```bash
   uvicorn app:app --reload
   ```

### Docker Deployment

1. Build the Docker image:

   ```bash
   docker build -t gluesync-scheduler-module:latest .
   ```

2. Run the container:

   ```bash
   docker run -p 1717:1717 -e CORE_HUB_URL=http://your-core-hub:1717 gluesync-scheduler-module:latest
   ```

## Configuration

Configure the application using environment variables:

| Variable | Description | Default |
|----------|-------------|----------|
| `CORE_HUB_URL` | URL of the Gluesync Core Hub (dynamically updated from SDK discovery if available) | `http://localhost:1717` |
| `HOST` | Host to bind the API server | `0.0.0.0` |
| `PORT` | Port to bind the API server | `1717` |
| `DEBUG` | Enable debug mode | `False` |
| `DB_URL` | Database connection URL | `sqlite:///./scheduler.db` |
| `ALLOWED_ORIGINS` | CORS allowed origins (comma-separated) | `*` |
| `CRONTAB_USER` | User for crontab operations (None for current user) | `None` |

### Gluesync SDK Configuration

Additional environment variables for the Gluesync SDK integration:

| Variable | Description | Default |
|----------|-------------|----------|
| `GLUESYNC_LICENSE_FILE` | Path to the Gluesync license file | `gs-license.dat` |
| `SSL_ENABLED` | Whether to use SSL for CoreHub connection | `False` |
| `GLUESYNC_KEYSTORE_PATH` | Path to JKS keystore file for SSL | `None` |
| `GLUESYNC_KEYSTORE_PASSWORD` | Password for JKS keystore | `None` |

> **Note**: The module identifier (`GLUESYNC_MODULE_TAG`) is hardcoded as `scheduler-module` and cannot be changed externally.

You can set these in a `.env` file in the project root.

## Running Locally and Testing with Postman

To run the Gluesync Scheduler Module (aka Chronos) locally and test the API with Postman, follow these steps:

### Setup Prerequisites

- Ensure Docker is installed on your machine.
- Ensure Python is installed on your machine.

### Environment Setup

1. **Set Up Environment Variables**:

   Create a `.env` file in the root of your project directory with the following content:

   ```text
   GLUESYNC_LICENSE_FILE=gs-license.dat
   GLUESYNC_MODULE_TAG=scheduler-module
   SSL_ENABLED=False
   GLUESYNC_SECURITY_CONFIG=/path/to/security-config.json
   DB_URL=sqlite:///./data/test_scheduler.db
   DEBUG=True
   CORE_HUB_URL=http://localhost:8080
   CRONTAB_USER=$USER
   HOST=0.0.0.0
   ```

### Running the Application

1. **Build and Run the Docker Container**:

   Use Docker to build and run the project with the following commands:

   ```bash
   docker build -t gluesync-scheduler-module .
   docker run -p 8080:8080 --env-file .env gluesync-scheduler-module
   ```

   This will start the application, and it should be accessible on `http://localhost:8080`.

### Testing the API with Postman

1. **Test with Postman**:

   Open Postman and create a new request.

   Set the request URL to `http://localhost:8080/your-endpoint`.

   Choose the appropriate HTTP method (GET, POST, etc.) and set any required headers or body data.

   Send the request and observe the response.

2. **Verify Logs and Outputs**:

   Check the terminal for logs to ensure the application is running correctly.

   If there are any issues, verify the Docker logs for more details.

By following these steps, you should be able to run the project locally and test the API using Postman. If you encounter any issues, feel free to ask for further assistance!

## API Documentation

Once the application is running, you can access the Swagger UI documentation at:

```plaintext
http://localhost:1717/docs
```

Or ReDoc at:

```plaintext
http://localhost:1717/redoc
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/jobs` | `GET` | List all scheduled jobs |
| `/api/jobs/{job_id}` | `GET` | Get a specific job |
| `/api/jobs` | `POST` | Create a new scheduled job |
| `/api/jobs/{job_id}` | `PUT` | Update an existing job |
| `/api/jobs/{job_id}` | `DELETE` | Delete a job |

## Scheduling Options

### User-Friendly Schedule Format

The scheduler supports a user-friendly schedule format that doesn't require knowledge of cron expressions:

```json
{
  "schedule": {
    "days_of_week": ["monday", "wednesday", "friday"],
    "hour": 8,
    "minute": 30
  }
}
```

Parameters:

- `days_of_week`: Array of days when the job should run. Valid values are: "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday". An empty array means every day.
- `hour`: Hour of the day (0-23)
- `minute`: Minute of the hour (0-59)

Examples:

- Every day at 8:30 AM: `{"days_of_week": [], "hour": 8, "minute": 30}`
- Every Monday, Wednesday, and Friday at 8:30 AM: `{"days_of_week": ["monday", "wednesday", "friday"], "hour": 8, "minute": 30}`
- Every weekend at midnight: `{"days_of_week": ["saturday", "sunday"], "hour": 0, "minute": 0}`

### Multi-Entity Support

The scheduler supports operating on multiple entities with a single job. Instead of creating separate jobs for each entity that follows the same schedule, you can specify an array of entity IDs:

```json
"entity_ids": ["entity-456", "entity-789", "entity-101"]
```

This is particularly useful for:

- Creating snapshots of multiple related entities at the same time
- Starting or stopping groups of entities together
- Ensuring operations across multiple entities are performed in a consistent timeframe


#### Example Job Creation Request

```json
POST /api/jobs

{
  "name": "Monday-Wednesday-Friday Job",
  "description": "Runs on specific days at 8:30 AM",
  "task_type": "entity_snapshot",
  "schedule": {
    "days_of_week": ["monday", "wednesday", "friday"],
    "hour": 8,
    "minute": 30
  },
  "pipeline_id": "pipeline-123",
  "entity_ids": ["entity-456", "entity-789"],
  "with_snapshot": true,
  "enabled": true
}
```

This will automatically be converted to the cron expression `30 8 * * 1,3,5` internally.

### Cron Expression Format

The scheduler also supports standard cron expressions for more advanced scheduling needs:

```plaintext
┌───────────── minute (0 - 59)
│ ┌───────────── hour (0 - 23)
│ │ ┌───────────── day of the month (1 - 31)
│ │ │ ┌───────────── month (1 - 12)
│ │ │ │ ┌───────────── day of the week (0 - 6) (Sunday to Saturday)
│ │ │ │ │
│ │ │ │ │
* * * * *
```

Examples:

- `0 * * * *` - Run once every hour
- `0 0 * * *` - Run once a day at midnight
- `0 0 * * 0` - Run once a week on Sunday at midnight
- `0 0 1 * *` - Run once a month on the 1st at midnight

#### Day of Week Mapping

In cron expressions, days of the week are represented as numbers:

| Day | Number |
|-----|--------|
| Sunday | 0 |
| Monday | 1 |
| Tuesday | 2 |
| Wednesday | 3 |
| Thursday | 4 |
| Friday | 5 |
| Saturday | 6 |

#### Scheduling for Specific Days

To schedule jobs for specific days of the week, you can use comma-separated values in the day of week field:

- `0 8 * * 1,3,5` - Run at 8:00 AM on Monday, Wednesday, and Friday
- `30 18 * * 1-5` - Run at 6:30 PM on weekdays (Monday through Friday)
- `0 12 * * 0,6` - Run at 12:00 PM on weekends (Saturday and Sunday)

#### Weekend Scheduling

To schedule a job to run only on weekends, you can use either of these approaches:

**Using Cron Expression:**

```plaintext
0 HH MM * * 0,6
```

Where `HH` is the hour (0-23) and `MM` is the minute (0-59).

For example, to run a job every Saturday and Sunday at 8:30 AM:

```plaintext
30 8 * * 0,6
```

**Using User-Friendly Schedule:**

```json
{
  "schedule": {
    "days_of_week": ["saturday", "sunday"],
    "hour": 8,
    "minute": 30
  }
}
```

The system will automatically convert this to the correct cron expression (`30 8 * * 0,6`).

## License

This project is dual-licensed under the following licenses:

1. **GNU Affero General Public License (AGPL) v3**
   - This is a free, copyleft license that allows you to use, modify, and distribute this software.
   - If you choose this option, any derivative works must also be licensed under AGPL v3.
   - See the [LICENSE-GPL](LICENSE-GPL) file for details.

2. **MOLO17 Commercial License**
   - For those who want to use this software in proprietary applications without the copyleft requirements of AGPL.
   - This option includes a warranty and permits proprietary use.
   - Contact MOLO17 at [info@molo17.com](mailto:info@molo17.com) for licensing terms and conditions.

You must choose one of these licenses to use this software. Using this software implies acceptance of one of these licenses.

## CoreHub Integration

This module now integrates directly with the Gluesync CoreHub using the official `gluesync_sdk`. The SDK provides:

- Secure WebSocket connection to the CoreHub
- Automatic handshake and authentication
- JWT token management for API calls
- Proper error handling for connection issues

The integration uses only the SDK-provided authentication token for all CoreHub API calls. Manual authentication with username/password is completely removed, making the module more secure and streamlined.

The module automatically retrieves the CoreHub URL from the SDK after discovery, ensuring that the correct URL is used even when the CoreHub is discovered dynamically through UDP broadcast.

## Testing

The module includes comprehensive tests to ensure functionality and reliability. Tests are organized into three categories:

1. **Cron Job Tests**: Tests the creation, updating, and execution of cron jobs through the scheduler module.
2. **CoreHub Integration Tests**: Tests the integration with the CoreHub API through the SDK.
3. **API Tests**: Tests the REST API endpoints for managing scheduled jobs.

### Running Tests Locally

To run the tests locally:

```bash
./tests/run_tests.sh
```

Or to run a specific test file:

```bash
./tests/run_tests.sh tests/test_cron_jobs.py
```

### Running Tests in Docker

To run tests in a Docker environment that simulates the CI environment:

```bash
./run_tests.sh --docker
```

### CI/CD Pipeline

The module uses GitLab CI/CD for continuous integration and deployment. The pipeline includes:

- **Test Stage**: Runs all tests with coverage reporting
- **Deploy Stage**: Builds and deploys the Docker image (only on tags)

See [CI_SETUP.md](CI_SETUP.md) for details on setting up the GitLab CI/CD variables.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the project
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
6. Make sure all tests pass in the CI pipeline

## Authors

- **MOLO17** - Initial work
- **Daniele Angeli** - Project author
