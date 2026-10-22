FROM ubuntu:24.04 AS dev
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build python3 libgtest-dev libbenchmark-dev clang libclang-rt-18-dev wrk \
    ca-certificates curl && rm -rf /var/lib/apt/lists/*
WORKDIR /src

FROM dev AS build
COPY . .
RUN cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build -j2 && ctest --test-dir build --output-on-failure

FROM ubuntu:24.04 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 pulsehttp
WORKDIR /app
COPY --from=build /src/build/pulsehttp_server /usr/local/bin/pulsehttp
COPY www /app/www
USER 10001
EXPOSE 8080
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/pulsehttp"]
CMD ["--address", "0.0.0.0", "--document-root", "/app/www"]
