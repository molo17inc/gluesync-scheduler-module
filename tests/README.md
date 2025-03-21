# Gluesync Scheduler Module Tests

This directory contains tests for the Gluesync Scheduler Module. These tests are designed to verify the functionality of the scheduler module, including the creation, updating, and execution of cron jobs.

## Test Structure

The tests are organized into three main categories:

1. **Cron Job Tests** (`test_cron_jobs.py`): Tests the creation, updating, and execution of cron jobs through the scheduler module.
2. **CoreHub Integration Tests** (`test_corehub_integration.py`): Tests the integration with the CoreHub API through the SDK.
3. **API Tests** (`test_api.py`): Tests the REST API endpoints for managing scheduled jobs.

## Running Tests Locally

To run the tests locally, you'll need to have Python 3.9+ installed and the required dependencies.

### Setup

1. Install the required dependencies:

   ```bash
   pip install -r requirements.txt
   pip install pytest pytest-cov python-dotenv
   ```

2. Create a test environment file (`.env.test`) with the following variables:

   ```env
   DB_URL=sqlite:///./tests/data/test_scheduler.db
   DEBUG=True
   CORE_HUB_URL=http://localhost:8080
   GLUESYNC_LICENSE_FILE=gs-license.dat
   GLUESYNC_MODULE_TAG=scheduler-module
   GLUESYNC_USE_SSL=False
   ```

3. Create a mock license file for testing:

   ```bash
   echo "mock-license-content" > gs-license.dat
   ```

### Running Tests

You can use the provided scripts to run tests:

```bash
# Run all tests
./run_tests.sh

# Run a specific test file
./run_tests.sh tests/test_cron_jobs.py

# Run tests in Docker environment
./run_tests.sh --docker
```

Or use pytest directly:

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_cron_jobs.py -v

# Run tests with coverage report
pytest tests/ -v --cov=. --cov-report=term --cov-report=html:coverage-report
```

### Test Scripts

The test scripts are organized as follows:

- `run_tests.sh`: Main wrapper script in the root directory
- `tests/run_tests.sh`: Script to run tests with proper environment setup
- `tests/scripts/run_single_test.sh`: Script to run a single test file
- `tests/scripts/run_tests_in_docker.sh`: Script to run tests in Docker environment

## CI/CD Integration

These tests are integrated into the GitLab CI/CD pipeline. The pipeline is configured to run the tests automatically on merge requests and when changes are pushed to the main branch.

The CI/CD pipeline includes the following stages:

1. **Unit Tests**: Runs the CoreHub integration tests.
2. **API Tests**: Tests the REST API endpoints.
3. **Cron Job Tests**: Tests the creation and execution of cron jobs.

## Test Environment

The tests use a SQLite database for testing, which is created and destroyed during the test run. The tests also mock the CoreHub API to avoid dependencies on external services.

## Adding New Tests

When adding new tests, please follow these guidelines:

1. Place the test file in the `tests/` directory.
2. Use the appropriate test category (cron job, CoreHub integration, or API).
3. Follow the existing test structure and naming conventions.
4. Ensure that tests clean up after themselves to avoid affecting other tests.
5. Add appropriate assertions to verify the expected behavior.
6. Update this README if necessary.
