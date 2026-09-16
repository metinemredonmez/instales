FROM debian:bookworm-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends nsis nsis-pluginapi >/dev/null && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["makensis"]
