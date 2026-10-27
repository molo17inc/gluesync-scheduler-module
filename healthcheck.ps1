# This file is part of Gluesync Scheduler Module (aka Chronos).
#
# Gluesync Scheduler Module (aka Chronos) is dual-licensed under the following licenses:
#
# 1. GNU General Public License (GPL) Version 3
#    You may use, modify, and distribute this software under the terms of the GPL v3.
#    See the LICENSE-GPL file or <http://www.gnu.org/licenses/gpl-3.0.html> for details.
#    This option is available at no cost, but any derivative works must also be licensed under GPL v3.
#
# 2. MOLO17 Commercial License
#    Alternatively, you may use this software under the MOLO17 Commercial License,
#    which includes a warranty and permits proprietary use. Contact MOLO17 at info@molo17.com
#    for licensing terms and conditions.
#
# You must choose one of these licenses to use this software. Using this software implies
# acceptance of one of these licenses. See the accompanying LICENSE files or contact
# MOLO17 for more information.
#
# Copyright (C) 2026 MOLO17. All rights reserved.

# Container health probe for Chronos on Windows (Docker HEALTHCHECK, PowerShell 7).
#
# Mirrors healthcheck.py: calls GET /health on the local server and exits 0 on HTTP 200,
# 1 otherwise. The final image has no Python, so this runs through pwsh.
#
# HOST / PORT select the address (0.0.0.0 and :: are probed through loopback; PORT
# defaults to 8000 like run_scheduler.py). When SSL_ENABLED is true the server serves
# HTTPS only if it found a certificate and key, otherwise it falls back to HTTP, so the
# probe tries HTTPS first and retries over HTTP only when the TLS connection fails. A
# timeout is never retried. The certificate is not verified: the target is local.

param([int]$TimeoutSec = 5)

$ErrorActionPreference = 'Stop'

$port = if ($env:PORT) { $env:PORT } else { '8000' }

$probeHost = if ($env:HOST) { $env:HOST.Trim() } else { '' }
if ($probeHost -eq '' -or $probeHost -eq '0.0.0.0') { $probeHost = '127.0.0.1' }
elseif ($probeHost -eq '::') { $probeHost = '[::1]' }
elseif ($probeHost.Contains(':') -and -not $probeHost.StartsWith('[')) { $probeHost = "[$probeHost]" }

$sslEnabled = $env:SSL_ENABLED -and (@('true', '1', 't') -contains $env:SSL_ENABLED.Trim().ToLower())
$schemes = if ($sslEnabled) { @('https', 'http') } else { @('http') }

$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)

foreach ($scheme in $schemes) {
    $remaining = [int][Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalSeconds)
    if ($remaining -le 0) { break }

    $uri = "${scheme}://${probeHost}:${port}/health"
    try {
        $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec $remaining `
            -SkipCertificateCheck -SkipHttpErrorCheck -MaximumRedirection 0
        if ($response.StatusCode -eq 200) { exit 0 }
        [Console]::Error.WriteLine("healthcheck: $uri returned HTTP $($response.StatusCode)")
        exit 1
    }
    catch {
        $message = $_.Exception.Message
        [Console]::Error.WriteLine("healthcheck: $uri $message")
        # A timeout means the server is not answering: unhealthy whatever the scheme
        if ($message -match 'timeout|timed out|canceled') { exit 1 }
        # Otherwise (TLS refused, connection reset) try the next scheme, if any
    }
}

exit 1
