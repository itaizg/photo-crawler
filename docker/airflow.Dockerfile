# Airflow 3.4 on Python 3.13 (research R1), plus the face_index package and cached model weights.
FROM apache/airflow:3.4.0-python3.13

ARG AIRFLOW_VERSION=3.4.0
ARG PYTHON_VERSION=3.13
ENV DEEPFACE_HOME=/opt/deepface \
    TF_CPP_MIN_LOG_LEVEL=2

USER root
# /opt/airflow/secrets seeds the `airflow-secrets` volume; group 0 lets any AIRFLOW_UID write it.
RUN mkdir -p /opt/deepface /opt/face_index /opt/airflow/secrets \
 && chown airflow: /opt/deepface /opt/face_index \
 && chown airflow:0 /opt/airflow/secrets && chmod 770 /opt/airflow/secrets
USER airflow

# Python runs through uv (constitution): the base image ships uv and makes Airflow's interpreter the
# active venv ($VIRTUAL_ENV=/home/airflow/.local), so `uv pip` installs into it and
# `uv run --no-project` runs in it. face_index goes into that interpreter because the DAG imports it.

# 1. Third-party dependencies from uv.lock, in their own cached layer. They install under Airflow's
#    constraints; uv.lock is resolved to agree with them ([tool.uv] constraint-dependencies in
#    pyproject.toml), so a lock that drifts from Airflow's pins fails here instead of upgrading Airflow.
COPY --chown=airflow: pyproject.toml uv.lock /opt/face_index/
RUN uv export --locked --no-dev --no-emit-project --project /opt/face_index \
      --output-file /opt/face_index/requirements.txt \
 && uv pip install --no-cache \
      --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt" \
      --requirements /opt/face_index/requirements.txt

# 2. The package itself, then model weights cached at build time so runs never download them (research R9).
COPY --chown=airflow: src /opt/face_index/src
COPY --chown=airflow: README.md /opt/face_index/
RUN uv pip install --no-cache --no-deps /opt/face_index
COPY --chown=airflow: docker/warm_models.py /opt/face_index/warm_models.py
RUN uv run --no-project python /opt/face_index/warm_models.py

COPY --chown=airflow: dags /opt/airflow/dags
