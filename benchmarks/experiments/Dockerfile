FROM pulsehttp-dev
RUN apt-get update && apt-get install -y --no-install-recommends git linux-tools-generic \
    && rm -rf /var/lib/apt/lists/*
# The distro wrapper insists on an exact running-kernel match; use the packaged binary
# directly. Record its version and any unsupported events in each profile artifact.
RUN python3 -c 'from pathlib import Path; p=sorted(Path("/usr/lib/linux-tools").glob("*/perf")); assert p; Path("/usr/local/bin/perf").symlink_to(p[-1])'
WORKDIR /src
