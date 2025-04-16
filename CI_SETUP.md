# GitLab CI Setup for Gluesync Scheduler Module (aka Chronos)

This document describes how to set up the GitLab CI/CD pipeline for the Gluesync Scheduler Module (aka Chronos).

## Required CI/CD Variables

The following CI/CD variables need to be set in the GitLab project settings:

### License Variables

- `GLUESYNC_LICENSE_CONTENT`: The content of the Gluesync license file. This will be written to a file during the CI/CD pipeline execution.

### Docker Registry Variables

- `DOCKER_REGISTRY`: The Docker registry URL (e.g., `registry.gitlab.com/molo17/gluesync`).
- `APP_NAME`: The name of the application (e.g., `chronos`).
- `CI_REGISTRY_PASSWORD`: The password for the Docker registry.

## Setting Up CI/CD Variables

1. Go to your GitLab project.
2. Navigate to **Settings > CI/CD**.
3. Expand the **Variables** section.
4. Add each of the required variables:
   - Click **Add Variable**.
   - Enter the variable name (e.g., `GLUESYNC_LICENSE_CONTENT`).
   - Enter the variable value.
   - For sensitive values like `GLUESYNC_LICENSE_CONTENT` and `CI_REGISTRY_PASSWORD`, make sure to check the **Mask variable** option to prevent the value from being displayed in job logs.
   - Click **Add Variable** to save.

## Pipeline Stages

The CI/CD pipeline consists of the following stages:

1. **Test**: Runs the unit tests, API tests, and cron job tests.
2. **Deploy**: Builds and deploys the Docker image to the registry.

## Test Stage

The test stage runs the following jobs:

- **unit_tests**: Runs the CoreHub integration tests.
- **api_tests**: Tests the REST API endpoints.
- **cron_job_tests**: Tests the creation and execution of cron jobs.

Each test job produces a coverage report and JUnit XML report for test results.

## Deploy Stage

The deploy stage builds the Docker image and pushes it to the registry. This stage only runs when a tag is pushed to the repository.

## Running Tests Locally

To run the tests locally in a Docker environment that simulates the CI environment, use the provided wrapper script with the `--docker` flag:

```bash
export GLUESYNC_LICENSE_CONTENT="your-license-content"
./run_tests.sh --docker
```

Alternatively, you can run the tests directly:

```bash
export GLUESYNC_LICENSE_CONTENT="your-license-content"
./tests/run_tests.sh
```
