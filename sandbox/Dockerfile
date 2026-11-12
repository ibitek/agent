FROM python@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535
RUN pip install --no-cache-dir pytest==8.3.5 \
    && groupadd --gid 10001 synvio \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin synvio
ENV HOME=/tmp TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8
WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["python"]
