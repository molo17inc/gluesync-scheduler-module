# Gluesync Scheduler Module

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![License](https://img.shields.io/badge/license-AGPL--3.0-green)

A backend service that provides a set of REST APIs for scheduling and managing cron jobs to automate Gluesync tasks, including starting/stopping entities and pipelines, as well as running snapshots.

## Features

- **Comprehensive REST API**: Full CRUD operations for scheduled jobs
- **Flexible Job Scheduling**: Based on standard cron expressions
- **Multiple Task Types**:
  - Start/stop entities
  - Start/stop entire pipelines
  - Schedule snapshots for entities
  - More task types can be easily added
- **Job Management**: View, create, update, disable/enable, and delete scheduled jobs
- **Containerized Deployment**: Docker support for easy deployment

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
| `CORE_HUB_URL` | URL of the Gluesync Core Hub | `http://localhost:1717` |
| `DEFAULT_USER` | Default user for Core Hub authentication | `admin` |
| `DEFAULT_PASSWORD` | Default password for Core Hub authentication | `admin` |
| `HOST` | Host to bind the API server | `0.0.0.0` |
| `PORT` | Port to bind the API server | `1717` |
| `DEBUG` | Enable debug mode | `False` |
| `DB_URL` | Database connection URL | `sqlite:///./scheduler.db` |
| `ALLOWED_ORIGINS` | CORS allowed origins (comma-separated) | `*` |
| `CRONTAB_USER` | User for crontab operations (None for current user) | `None` |

You can set these in a `.env` file in the project root.

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

## Cron Expression Format

The scheduler uses standard cron expressions:

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

## License

This project is licensed under the [GNU Affero General Public License (AGPL) v3](LICENSE).

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the project
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Authors

- **MOLO17** - Initial work
- **Daniele Angeli** - Project author
