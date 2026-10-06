FROM mcr.microsoft.com/playwright:v1.56.1-noble@sha256:f1e7e01021efd65dd1a2c56064be399f3e4de00fd021ac561325f2bfbb2b837a
WORKDIR /opt/driver
RUN npm install --ignore-scripts --no-audit --no-fund playwright@1.56.1
COPY backend/tests/browser_gateway.mjs ./run.mjs
COPY backend/tests/browser_pages.mjs ./pages.mjs
CMD ["node", "run.mjs"]
