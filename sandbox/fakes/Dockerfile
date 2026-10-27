FROM node:24.16.0-alpine
WORKDIR /fake
# ws comes from vendor/node_modules when the sandbox staged it there (CI: the npm registry is not
# reachable, the CI image carries ws, see stageFakeDeps in lib/stack.mjs); otherwise from npm.
COPY vendor/ ./vendor/
RUN if [ -d vendor/node_modules/ws ]; then mv vendor/node_modules node_modules; \
    else npm init -y >/dev/null && npm install --no-audit --no-fund ws@8.18.0 >/dev/null; fi \
    && rm -rf vendor
COPY fake-corehub.mjs ./fake-corehub.mjs
HEALTHCHECK --interval=3s --timeout=2s --start-period=3s --retries=10 CMD test -f /tmp/ready
ENTRYPOINT ["node", "/fake/fake-corehub.mjs"]
