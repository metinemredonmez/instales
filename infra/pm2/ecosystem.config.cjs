// pm2 ecosystem for a plain Ubuntu host (no Docker). Ports are chosen not to collide with the
// other apps on the box: API 8010, web (nginx) 8088. Change API_PORT/WEB_PORT in the nginx site too.
const ROOT = "/opt/instilens";
module.exports = {
  apps: [
    {
      name: "instilens-api",
      cwd: `${ROOT}/backend`,
      script: ".venv/bin/uvicorn",
      args: "instilens.api.main:app --host 127.0.0.1 --port 8010 --workers 2 --proxy-headers --forwarded-allow-ips=127.0.0.1",
      interpreter: "none",
      env: { INSTILENS_ENVIRONMENT: "production" },
      max_memory_restart: "600M",
      autorestart: true,
    },
    {
      name: "instilens-scheduler",
      cwd: `${ROOT}/backend`,
      script: ".venv/bin/instilens",
      args: "scheduler",
      interpreter: "none",
      env: { INSTILENS_ENVIRONMENT: "production" },
      max_memory_restart: "900M",
      autorestart: true,
    },
  ],
};
