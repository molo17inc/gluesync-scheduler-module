# Gluesync Chronos module (Chronos)

A scheduling module for Gluesync pipelines, allowing automated execution of pipeline operations based on cron schedules. (aka Chronos)

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![License](https://img.shields.io/badge/license-Dual-green)

A backend service that provides a set of REST APIs for scheduling and managing cron jobs to automate Gluesync tasks, including starting/stopping entities and pipelines, as well as running snapshots.

## Versioning

The project follows [Semantic Versioning](https://semver.org/) (SemVer) for version numbering. The version is automatically managed during the CI/CD build process and is included in the application's output at startup.

### How Versioning Works

1. **Build Process**:
   - The version is extracted from the Git tag during the CI/CD build process
   - A `VERSION` file is created with the version string
   - The file is included in the Docker image at `/app/VERSION`

2. **Runtime**:
   - The application reads the version from the `VERSION` file at startup
   - If the file doesn't exist, it defaults to `0.0.0-dev`
   - The version is displayed in the application logs at startup

3. **Version Display**:

   ```text
   ================================================================================
   Gluesync Chronos module v1.2.3
   ================================================================================
   ```

### Version File Location

- **Linux Containers**: `/app/VERSION`
- **Windows Containers**: `C:\app\VERSION`

## Features

- **Comprehensive REST API**: Full CRUD operations for scheduled jobs
- **Flexible Job Scheduling**: Choose between user-friendly schedule format or standard cron expressions
- **In-Memory Job Scheduling**: Uses APScheduler for reliable job execution in containerized environments
- **Configurable Settings**: Store and retrieve application settings via API, including timezone configuration
- **Multiple Task Types**:
  - Start/stop entities
  - Start/stop entire pipelines
  - Start/stop entity groups
  - Schedule snapshots for entities, pipelines, or groups
  - Run a Query Studio SQL query (`query_studio`) on schedule, platform event, or webhook
  - More task types can be easily added
- **Job Management**: View, create, update, disable/enable, and delete scheduled jobs
- **Containerized Deployment**: Docker support for easy deployment
- **CoreHub Integration**: Direct SDK integration with Gluesync CoreHub for secure authentication and communication

## Prerequisites

- Python 3.8+
- Gluesync Core Hub instance

## Job Scheduling Architecture

The module uses APScheduler for reliable in-memory job scheduling, which is particularly well-suited for containerized environments:

- **In-Memory Scheduler**: Jobs are stored and executed in memory, eliminating the need for system crontab access
- **Persistent Storage**: Job definitions are stored in the database for persistence across restarts
- **Automatic Recovery**: On application startup, all enabled jobs are automatically loaded from the database into the scheduler
- **Timezone Support**: All jobs respect the configured timezone setting
- **Detailed Logging**: Each job execution is logged with timestamps and results

This architecture ensures that jobs continue to run reliably even in containerized environments where traditional cron services may not be available or may lose state between container restarts.

## Project Structure

The project follows a standard Python package structure:

```python
gluesync-scheduler-module/
├── gluesync_scheduler/         # Main package directory
│   ├── api/                   # API endpoints and routers
│   ├── cli/                   # Command-line interfaces
│   ├── config/                # Configuration settings
│   ├── core/                  # Core application logic
│   ├── db/                    # Database models and connections
│   ├── models/                # Data models and schemas
│   ├── services/              # Business logic services
│   └── utils/                 # Utility functions
├── tests/                     # Test directory
├── data/                      # Data storage directory
├── logs/                      # Log files directory
├── main.py                    # Main entry point
├── run_scheduler.py           # Script to run the scheduler
├── run_job.sh                 # Script to run jobs
├── setup.py                   # Package setup file
└── requirements.txt           # Dependencies
```

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

3. Install the package in development mode:

   ```bash
   pip3 install -e .
   ```

4. Run the application:

   ```bash
   python3 run_scheduler.py
   ```

### Docker Deployment

1. Build the Docker image:

   ```bash
   docker build -t gluesync-scheduler-module:latest .
   ```

2. Run the container:

   ```bash
   docker run -p 1717:1717 -e GLUESYNC_HOST=http://your-core-hub:1717 gluesync-scheduler-module:latest
   ```

## Configuration

Configure the application using environment variables:

| Variable | Description | Default |
|----------|-------------|----------|
| `GLUESYNC_HOST` | URL of the Gluesync Core Hub (dynamically updated from SDK discovery if available) | `http://localhost:1717` |
| `HOST` | Host to bind the API server | `0.0.0.0` |
| `PORT` | Port to bind the API server | `1717` |
| `DEBUG` | Enable debug mode for Uvicorn server logs | `False` |
| `LOG_LEVEL` | Application logging level (DEBUG, INFO, WARNING, ERROR) | `INFO` |
| `DB_URL` | Database connection URL | `sqlite:///./scheduler.db` |
| `DATA_DIR` | Directory for storing application data | `/app/data` |
| `ENTITY_START_TIMEOUT` | Timeout in seconds for entity start operations | `2` |
| `ALLOWED_ORIGINS` | CORS allowed origins (comma-separated) | `*` |
| `CRONTAB_USER` | User for crontab operations (None for current user) | `None` |
| `CHRONOS_SDK_TOKEN_REFRESH_ON_401` | Whether to automatically refresh the SDK token on 401 Unauthorized and retry outbound CoreHub calls exactly once | `True` |
| `CHRONOS_REDO_PAUSE_TIMEOUT` | Maximum seconds to wait for entities to leave the Active state (Hold or Error accepted) before issuing a redo command. See [Pause before redo](#pause-before-redo). | `60` |
| `CHRONOS_REDO_POLL_INTERVAL` | Seconds between CoreHub `entities-status` checks while polling for entities to leave the Active state (Hold or Error accepted) before a redo. See [Pause before redo](#pause-before-redo). | `5` |
| `TZ` | Preferred timezone environment variable for job scheduling. If set, it takes precedence over `TIMEZONE`. | `UTC` |
| `TIMEZONE` | Deprecated timezone environment variable for job scheduling. Used only as fallback when `TZ` is not set. | `UTC` |

### Gluesync SDK Configuration

Additional environment variables for the Gluesync SDK integration:

| Variable | Description | Default |
|----------|-------------|----------|
| `GLUESYNC_LICENSE_FILE` | Path to the Gluesync license file | `gs-license.dat` |
| `SSL_ENABLED` | Whether to use SSL for CoreHub connection | `False` |
| `SSL_SKIP_VERIFY` | Skip SSL certificate verification | `True` |
| `GLUESYNC_SECURITY_CONFIG` | Path to security configuration file | `/opt/gluesync/data/security-config.json` |
| `GLUESYNC_KEYSTORE_PATH` | Path to JKS keystore file for SSL | `None` |
| `GLUESYNC_KEYSTORE_PASSWORD` | Password for JKS keystore | `None` |

> **Note**: The module identifier (`GLUESYNC_MODULE_TAG`) is hardcoded as `chronos` and cannot be changed externally.

### Authorization / user role enforcement

Chronos protects its `/api/jobs/*` and `/api/settings/*` endpoints by
resolving the caller's identity via CoreHub's `GET /auth/me` on each
request (with a short-lived cache) and rejecting requests that do not
carry a valid `gs-auth` session cookie or `Authorization: Bearer` JWT.
See [`docs/auth-plan.md`](docs/auth-plan.md) for the full design.

Browsers reach chronos through the same origin as CoreHub (Traefik),
so the `gs-auth` cookie set at login is forwarded automatically.
CLI / script callers must send `Authorization: Bearer <jwt>` themselves.

| Variable | Description | Default |
|----------|-------------|---------|
| `CHRONOS_AUTH_CACHE_TTL` | TTL (seconds) for cached `/auth/me` lookups. Revoke-latency ≤ this value. | `30` |
| `CHRONOS_AUTH_TIMEOUT_MS` | httpx timeout for outbound `/auth/me` calls. | `5000` |
| `CHRONOS_COREHUB_URL_OVERRIDE` | Force the base URL used for `/auth/me` instead of relying on SDK discovery. | *(unset)* |
| `CHRONOS_AUTH_FAIL_OPEN` | **DEBUG ONLY.** When `true`, every request is accepted as SUPER_ADMIN. Logs a warning on every request. Never enable in production. | `false` |

Permission matrix (mirrors CoreHub `UserRole` + gluesync-nodejs-monorepo UI):

| Guard | SUPER_ADMIN | MANAGER | MONITOR | VIEWER | EXTERNAL_MODULE |
|-------|-------------|---------|---------|--------|-----------------|
| Read jobs / read settings (`current_user`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Run / enable / disable schedule (`require_control`) | ✅ | ✅ | ✅ | ❌ | ❌ |
| Create / edit / delete schedule (`require_manage`) | ✅ | ✅ | ❌ | ❌ | ❌ |
| Change chronos configuration (`require_config`) | ✅ | ✅ | ❌ | ❌ | ❌ |

The legacy `/api/pipelines/*` routes are chronos-internal (invoked by
cron jobs) and remain protected by the existing `verify_localhost`
dependency, not by user roles.

You can set these in a `.env` file in the project root.

## Running Locally and Testing with Postman

To run the Gluesync Chronos module (aka Chronos) locally and test the API with Postman, follow these steps:

### Setup Prerequisites

- Ensure Docker is installed on your machine.
- Ensure Python is installed on your machine.

### Environment Setup

1. **Set Up Environment Variables**:

   Create a `.env` file in the root of your project directory with the following content:

   ```text
   GLUESYNC_LICENSE_FILE=gs-license.dat
   GLUESYNC_MODULE_TAG=chronos
   SSL_ENABLED=False
   GLUESYNC_SECURITY_CONFIG=/path/to/security-config.json
   DB_URL=sqlite:///./data/test_scheduler.db
   DEBUG=True
   GLUESYNC_HOST=http://localhost:8080
   CRONTAB_USER=$USER
   HOST=0.0.0.0
   FIRE_ONCE=False
   ```

   **Note**: Set `FIRE_ONCE=True` to have jobs automatically disabled after their first execution. The scheduler will retry connecting to CoreHub indefinitely with exponential backoff (1s, 2s, 4s, 8s, 16s, 30s max) until successful.

### Running the Application

1. **Build and Run the Docker Container**:

   Use Docker to build and run the project with the following commands:

   ```bash
   docker build -t gluesync-scheduler-module .
   docker run -p 8080:8080 --env-file .env gluesync-scheduler-module
   ```

   This will start the application, and it should be accessible on `http://localhost:8080`.

### Database Migrations

The Gluesync Chronos module automatically runs database migrations during startup, so you don't need to run them manually when starting the application. However, if you're upgrading an existing installation or need to run migrations separately, the project includes migration scripts for all schema changes.

#### Running Migrations Manually

```bash
# Run all migrations
./migrations/run_migrations.sh

# Specify a custom database URL
./migrations/run_migrations.sh --db-url sqlite:///path/to/your/database.db
```

#### Running Migrations in Docker

```bash
# Run all migrations inside the container
docker exec [container_name] ./migrations/run_migrations.sh
```

Current migrations:

- `migrate_add_settings_table.py`: Adds the settings table for configuration storage

### Testing the API with Postman

1. **Test with Postman**:

   Open Postman and create a new request.

   Set the request URL to `http://localhost:8080/your-endpoint`.

   Choose the appropriate HTTP method (GET, POST, etc.) and set any required headers or body data.

   Send the request and observe the response.

2. **Verify Logs and Outputs**:

   Check the terminal for logs to ensure the application is running correctly.

   If there are any issues, verify the Docker logs for more details.

## Settings Management

The module includes a settings management system that allows you to configure application settings through the API. Settings are stored in the database and persist across application restarts.

### Available Settings

| Setting Key | Description | Default |
|-------------|-------------|----------|
| `timezone`  | Timezone used for scheduling jobs | Value from `TZ` env var, or `TIMEZONE` if `TZ` is not set |

### Settings API Endpoints

#### Get All Settings

```http
GET /api/settings/
```

Returns a list of all configured settings.

#### Get a Specific Setting

```http
GET /api/settings/{key}
```

Retrieve a specific setting by its key.

#### Update a Setting

```http
PUT /api/settings/{key}
```

Update the value of a specific setting.

**Request Body**:

```json
{
  "value": "America/New_York",
  "description": "Updated timezone description" // Optional
}
```

#### Create a New Setting

```http
POST /api/settings/
```

Create a new custom setting.

**Request Body**:

```json
{
  "key": "custom_setting",
  "value": "custom_value",
  "description": "A custom application setting" // Optional
}
```

### Environment Variable Override

If no `timezone` setting exists in the database, the initial value is taken from environment variables (with `TZ` preferred and `TIMEZONE` used only as a deprecated fallback). Subsequent changes to the `timezone` setting via the API are persisted in the database and are not automatically overridden by environment variables on restart.

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
|----------|--------|-----------|
| `/api/jobs` | `GET` | List all scheduled jobs |
| `/api/jobs/{job_id}` | `GET` | Get a specific job |
| `/api/jobs` | `POST` | Create a new scheduled job |
| `/api/jobs/{job_id}` | `PUT` | Update an existing job |
| `/api/jobs/{job_id}` | `DELETE` | Delete a job |
| `/api/jobs/{job_id}/run` | `POST` | Manually trigger a job |
| `/api/jobs/{job_id}/status` | `PATCH` | Enable/disable a job |
| `/api/settings` | `GET` | List all settings |
| `/api/settings/{key}` | `GET` | Get a specific setting |
| `/api/settings` | `POST` | Create a new setting |
| `/api/settings/{key}` | `PUT` | Update a setting |
| `/api/query-studio/agents` | `GET` | List Query Studio agents from CoreHub (UI agent picker) |

## Query Studio on event (`query_studio`)

Chronos can run a Query Studio SQL query when a scheduled job (cron), a platform-event trigger flow, or an incoming webhook trigger flow fires.

Stored fields:

- `pipeline_id` — pipeline that hosts the target agent
- `agent_id` — Query Studio agent to execute against
- `query_sql` — SQL text (do **not** put SQL on the crontab command line). Required for a **custom query**. For a **saved query** this is a snapshot of the SQL at save time (the UI always sends it).
- `saved_query_id` — optional CoreHub saved-query id. When set, Chronos targets that saved query rather than custom SQL only.

A Query Studio task can target either:

- **Custom SQL**: `saved_query_id` null, `query_sql` required
- **Saved query**: `saved_query_id` set; `query_sql` is stored as a snapshot. If Hub is reachable, Chronos executes the live SQL from `GET {corehub}/query-studio/saved-queries/{id}` (and Hub `agentId` when present). On 404/403/failure it falls back to the stored snapshot. If both the live query and the snapshot are missing, the job fails.

On fire, Chronos calls CoreHub:

```http
POST {corehub}/query-studio/pipelines/{pipelineId}/agents/{agentId}/execute
Authorization: Bearer <SDK JWT>
Content-Type: application/json

{"sql": "<query text>"}
```

HTTP 2xx with Hub `status` `ERROR` (failed query) is treated as a Chronos job failure. The Hub call uses a 120 second timeout.

The Control Plane Scheduler UI should bind:

- an **agent picker** to `agent_id` (Chronos `GET /api/query-studio/agents` proxies CoreHub `GET /query-studio/agents`)
- a **saved-query picker** to `saved_query_id` (optional; omit for custom SQL)
- a **SQL code editor** to `query_sql` (custom SQL, or snapshot when targeting a saved query)

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

### Multi-Entity and Group Support

The scheduler supports operating on multiple entities or groups with a single job:

#### Entity Operations

Instead of creating separate jobs for each entity that follows the same schedule, you can specify an array of entity IDs:

```json
"entity_ids": ["entity-456", "entity-789", "entity-101"]
```

#### Group Operations

For group-level operations, you can specify an array of group IDs:

```json
"group_ids": ["group-critical", "group-analytics", "group-reporting"]
```

This is particularly useful for:

- Creating snapshots of multiple related entities or entire groups at the same time
- Starting or stopping groups of entities together
- Ensuring operations across multiple entities/groups are performed in a consistent timeframe
- Managing data synchronization at the group level for better organization

#### Available Task Types

The scheduler supports the following task types for job creation:

**Entity-Level Operations:**

- `entity_start` - Start synchronization for specific entities within a pipeline
- `entity_stop` - Stop synchronization for specific entities within a pipeline  
- `entity_snapshot` - Create a snapshot (one-time synchronization) for specific entities

**Pipeline-Level Operations:**

- `pipeline_start` - Start synchronization for an entire pipeline
- `pipeline_stop` - Stop synchronization for an entire pipeline
- `pipeline_snapshot` - Create a snapshot (one-time synchronization) for an entire pipeline

**Group-Level Operations:**

- `group_start` - Start synchronization for specific groups within a pipeline
- `group_stop` - Stop synchronization for specific groups within a pipeline
- `group_snapshot` - Create a snapshot (one-time synchronization) for specific groups

**Operation Parameters:**

- For entity operations: Requires `entity_ids` array
- For group operations: Requires `group_ids` array  
- For pipeline operations: No additional IDs needed (operates on entire pipeline)
- Snapshot operations support `with_snapshot` (boolean) and `snapshot_write_method` ("UPSERT" or "INSERT")

#### Example Job Creation Requests

**Entity-Level Job:**

```json
POST /api/jobs

{
  "name": "Monday-Wednesday-Friday Entity Job",
  "description": "Runs entity snapshots on specific days at 8:30 AM",
  "task_type": "entity_snapshot",
  "schedule": {
    "days_of_week": ["monday", "wednesday", "friday"],
    "hour": 8,
    "minute": 30
  },
  "pipeline_id": "pipeline-123",
  "entity_ids": ["entity-456", "entity-789"],
  "with_snapshot": true,
  "snapshot_write_method": "UPSERT",
  "enabled": true
}
```

**Group-Level Job:**

```json
POST /api/jobs

{
  "name": "Weekly Group Synchronization",
  "description": "Start synchronization for critical data groups every Monday",
  "task_type": "group_start",
  "schedule": {
    "days_of_week": ["monday"],
    "hour": 9,
    "minute": 0
  },
  "pipeline_id": "pipeline-123",
  "group_ids": ["group-critical", "group-analytics"],
  "with_snapshot": true,
  "snapshot_write_method": "UPSERT",
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

**Connection Behavior**: Whether using `GLUESYNC_HOST` for direct connection or UDP discovery, the scheduler will retry connecting to CoreHub indefinitely with exponential backoff (1s, 2s, 4s, 8s, 16s, 30s max) until successful. This ensures reliable operation in containerized environments where services may start in different orders.

### Pause before redo

All redo operations (`entity_redo`, `pipeline_redo`, `group_redo`) now pause their target objects **before** sending the redo command to CoreHub, so the target is fully stopped before the redo is applied. Instead of waiting a fixed number of seconds, Chronos polls CoreHub for the runtime status of the affected entities and only proceeds once every target entity is no longer **Active** — i.e. it reports either **Hold** (paused) or **Error**. Both states are acceptable to move forward with the redo; only an entity that is still syncing or snapshotting (`isSyncActive` or `isMigrationActive` true) keeps the poller waiting.

The polling uses CoreHub's `GET /pipelines/{pipeline_id}/entities-status` endpoint, which returns the same runtime status the MPP UI uses to render Active / Hold / Error. An entity is considered settled (ready to proceed) when both `isSyncActive` and `isMigrationActive` are false — regardless of whether `errorState` is set. This mirrors the CoreHub / MPP UI computation, where an entity is `active` only while syncing or snapshotting, and otherwise resolves to `hold` or `error`.

| Redo operation | Pause target | What is polled |
|----------------|--------------|----------------|
| `entity_redo` | The single entity (`stop` with `entity` param) | That entity leaving Active (Hold or Error) |
| `pipeline_redo` | The whole pipeline (`stop`) | All entities in the pipeline leaving Active (Hold or Error) |
| `group_redo` | The group (`stop-group`) | All entities belonging to the group leaving Active (Hold or Error) |

If the pause call itself fails, the redo is aborted. If polling times out before every target entity leaves the Active state, the redo is also aborted and an error is logged. When the affected entity IDs cannot be resolved (for example the config endpoint returns nothing), Chronos proceeds with the redo and logs a warning rather than blocking indefinitely.

The polling behavior is configurable through two environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CHRONOS_REDO_PAUSE_TIMEOUT` | `60` | Maximum seconds to wait for all target entities to leave the Active state before giving up. |
| `CHRONOS_REDO_POLL_INTERVAL` | `5` | Seconds between status checks while polling. |

## Testing

The module includes comprehensive tests to ensure functionality and reliability. Tests are organized into three categories:

1. **Cron Job Tests**: Tests the creation, updating, and execution of cron jobs through the scheduler module.
2. **CoreHub Integration Tests**: Tests the integration with the CoreHub API through the SDK.
3. **API Tests**: Tests the REST API endpoints for managing scheduled jobs.

### Running a Job Manually

You can manually trigger a job using the API:

```bash
curl -X POST "http://localhost:1717/api/jobs/{job_id}/run"
```

Or using the CLI module directly:

```bash
python3 -m gluesync_scheduler.cli.job_runner <job_identifier>
```

Or using the provided shell script:

```bash
./run_job.sh <job_identifier>
```

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
