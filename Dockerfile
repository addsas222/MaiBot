# Runtime image
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Working directory
WORKDIR /MaiMBot

ENV MAIBOT_LEGACY_0X_UPGRADE_CONFIRMED=1
ENV PATH="/MaiMBot/.venv/bin:${PATH}"

# Copy dependency metadata
COPY pyproject.toml uv.lock ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies
RUN uv sync --no-dev --no-install-project

# Install system libraries required by Playwright Chromium. The browser binary
# itself is downloaded lazily into the configured data directory at runtime.
RUN python -m playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Copy project source
COPY . .

# 适配器模板锁定到审计过的 commit，防止上游 main 漂移或被劫持时直接进入产物
RUN git clone https://github.com/Mai-with-u/MaiBot-Napcat-Adapter.git plugin-templates/MaiBot-Napcat-Adapter \
    && git -C plugin-templates/MaiBot-Napcat-Adapter checkout 443d6132f543e51c45adc89a2875c5d7744d65fa \
    && rm -rf plugin-templates/MaiBot-Napcat-Adapter/.git
RUN chmod +x docker-entrypoint.sh

# 非 root 运行：entrypoint 需要写 plugins/ 与 config/（compose 卷挂载）
RUN useradd --create-home maibot \
    && chown -R maibot:maibot /MaiMBot
USER maibot

EXPOSE 8000 8001

ENTRYPOINT [ "./docker-entrypoint.sh" ]
