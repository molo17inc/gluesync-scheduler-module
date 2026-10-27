# Image of the `sandbox` CI jobs (.gitlab-ci.yml, ci/sandbox-job.sh): docker:dind plus Node 24 and the
# one npm package the fake Core Hub needs, so the jobs download no package at run time. Hosted in
# Nexus (molo17-local), because the runners must not reach Docker Hub or the npm registry.
#
# Rebuild and push, from the repository root, whenever this file or the `ws` version in
# sandbox/fakes/Dockerfile changes; bump the tag here, in .gitlab-ci.yml and in sandbox/README.md
# ("In CI") together:
#
#   docker buildx build --platform linux/amd64,linux/arm64 --provenance=false --sbom=false \
#     -f ci/sandbox-ci.Dockerfile \
#     --build-arg SOURCE_COMMIT=$(git rev-parse --short HEAD) \
#     -t nexus.hq.molo17.com/molo17-local/chronos-sandbox-ci:1 --push .

# Node 24 is not in Alpine 3.22's packages (nodejs is 22, nodejs-current is 23), so the binary comes from
# the official Node image that the sandbox already pins (sandbox/lib/env.mjs, fakes/Dockerfile); it is
# musl-built, like this image. The second use of it fetches `ws` for the fake Core Hub.
FROM nexus.hq.molo17.com/molo17-local/node:24.16.0-alpine AS node
ARG WS_VERSION=8.18.0
RUN npm install --prefix /deps --no-audit --no-fund ws@${WS_VERSION}

FROM nexus.hq.molo17.com/molo17-local/docker:28.3.3-dind

# The job runs dockerd inside this container (docker-in-docker, privileged), and dockerd needs root.
USER root

ARG WS_VERSION=8.18.0
ARG SOURCE_COMMIT=unknown

LABEL org.opencontainers.image.title="chronos-sandbox-ci" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}"

# The shared libraries the node binary links against.
RUN apk add --no-cache libstdc++ libgcc
COPY --from=node /usr/local/bin/node /usr/local/bin/node
# The sandbox's only dependency (staged into the fake's build context by lib/stack.mjs, which reads
# SANDBOX_DEPS_DIR, default /opt/sandbox-deps).
COPY --from=node /deps/node_modules /opt/sandbox-deps/node_modules

# Node must be 24, and `ws` must be the version sandbox/fakes/Dockerfile installs when it runs without
# the staged copy.
RUN node --version | grep -q '^v24\.' || { echo "expected Node 24, got $(node --version)"; exit 1; }
COPY sandbox/fakes/Dockerfile /tmp/fakes.Dockerfile
RUN grep -q "ws@${WS_VERSION}" /tmp/fakes.Dockerfile \
 || { echo "sandbox/fakes/Dockerfile does not install ws@${WS_VERSION}: rebuild with --build-arg WS_VERSION"; exit 1; }
RUN rm /tmp/fakes.Dockerfile \
 && node -e 'const v=require("/opt/sandbox-deps/node_modules/ws/package.json").version; if (v!==process.argv[1]) {console.error("ws is "+v); process.exit(1)}' "${WS_VERSION}"
