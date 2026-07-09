# Welcome to your Gluesync trial

Thank you for trying Gluesync. This package contains everything you need to get started.

If you're reading this you're about to try out Gluesync and we'd like to provide you with the smoothest journey possible in evaluating our product, so... let's begin!

## Trial license agreement

By proceeding further with the evaluation of our Product you explicitly confirm that you've read and acknowledged our Trial Agreement (`trial-agreement.pdf`) present in the root folder of this kit.

### Limited 30 days trial

This kit contains a valid license of Gluesync with a limited 30-day trial period. The trial period extension comes at the sole discretion of the MOLO17 sales team, the same applies to NFR licenses. Please write an email to our sales representative at <sales@molo17.com>.

## Trial kit 101

Based on the Trial kit flavor you've selected here you can find the steps to follow.

### Platform installation

For the simplest installation experience, follow the official guide here: <https://docs.molo17.com/gluesync/v2.1/deploy-and-run/default-installers.html>

DIY/advanced options: a dedicated section is available in the docs (see “DIY installers” in the page above) for manual/advanced setups.

Should you have any doubts or issues, please launch the `pre-flight-checker.sh` (or its Windows equivalent `pre-flight-checker.ps1`) script and send the generated zip file to our support team at <support@molo17.com>.

To learn more about the Docker compose kit and how to get the most out of it, we suggest you check out the following link from our online documentation: https://www.molo17.com/docs/gluesync/v2.0/deploy-and-run/docker-compose.html.

#### Deployment options

This trial kit supports multiple environments:

- **Linux containers**: Standard deployment for Linux hosts
- **Windows Server 2019**: Uses `latest-win-nanoserver-ltsc2019` image tags, optimized for Windows Server 2019
- **Windows Server 2022+**: Uses `latest-win-nanoserver-ltsc2022` image tags, optimized for Windows Server 2022 and later versions

All Windows deployments include PowerShell scripts for log collection and system verification, and are compatible with Windows containers on Windows hosts.

### Running the platform

Quick CLI paths (scripts included in every default setup):

- **Run**: `./run.sh` (Linux/macOS) or `./run.ps1` (Windows)
- **Stop**: `./stop.sh` or `./stop.ps1`
- **Update**: `./update.sh` or `./update.ps1` (updates are also available directly from the Control Plane UI when the platform is running)

Default control plane credentials are:

- Username: `admin`.
- Password: `admin`.

NOTE: The _run_ scripts are only supposed to be ran once, as the services will automatically after system reboot.

### Kubernetes

Gluesync can be deployed on Kubernetes using our provided Helm charts. Here's a quick guide for both local development (minikube) and cloud deployment (GKE).

#### Prerequisites

Install required tools:

- [kubectl](https://kubernetes.io/docs/tasks/tools/install-kubectl/)
- [Helm](https://helm.sh/docs/intro/install/)
- For local development: [minikube](https://minikube.sigs.k8s.io/docs/start/)
- For GKE: [gcloud CLI](https://cloud.google.com/sdk/docs/install-sdk)

Refer to this guide for more details: [Gluesync Kubernetes Deployment](https://docs.molo17.com/gluesync/v2.1/deploy-and-run/kubernetes.html)

### Monitor your environment

To monitor and ease the management of this kit we added [Portainer](https://portainer.io) as a Web GUI companion. To access Portainer, log into your web browser by following this link: https://localhost:9443, then enter:

- Username: admin
- Password: passwordpassword

## Provide feedback

Any feedback you have is greatly welcome and encouraged: please write us an email at [feedback@molo17.com](mailto:feedback@molo17.com) and tell us what you think about Gluesync.

## Official product documentation

You can find the official product documentation here at this link: [Gluesync official documentation](https://docs.molo17.com/gluesync).

## Get support

We offer full-fledged support even to users who are evaluating our product, to receive support please visit our [support portal](https://support.molo17.com) and raise a ticket by clicking on the *Gluesync* tab.

### Log collection

If you encounter issues, you can collect and send logs directly from the Control Plane UI: **Settings → Collect & Send Logs** (recommended).

CLI option (if you prefer scripts or are running headless):

- **`collect-logs.sh`**: For Linux and macOS systems.
- **`collect-logs.ps1`**: For Windows systems.

#### Basic usage

Running these scripts will create a timestamped archive (e.g., `support-logs-v1.0-20230929-143000.zip`) containing all the necessary diagnostic information from your Gluesync installation.

```bash
# Linux/macOS
./collect-logs.sh

# Windows
./collect-logs.ps1
```

#### Direct upload to support

For faster support resolution, you can upload logs directly to our FTP server:

```bash
# Linux/macOS with upload
./collect-logs.sh -e your@email.com -t TICKET123

# Windows with upload
./collect-logs.ps1 -Email your@email.com -Ticket TICKET123
```

**Requirements for upload:**

- Valid email address and support ticket number
- Internet connectivity
- If any requirement is missing, the script will create the archive locally

The upload uses your ticket number as username and email as password to securely transmit logs directly to MOLO17 support.
