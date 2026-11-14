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

# 1. Third-party dependencies, in their own cached layer. Libraries shared with Airflow follow
#    Airflow's constraints; TensorFlow and DeepFace resolve within the bounds in pyproject.toml.
COPY --chown=airflow: pyproject.toml /opt/face_index/
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('/opt/face_index/pyproject.toml','rb'))['project']['dependencies']))" \
      > /opt/face_index/requirements.txt \
 && pip install --no-cache-dir \
      --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt" \
      "apache-airflow==${AIRFLOW_VERSION}" -r /opt/face_index/requirements.txt

# 2. Model weights, cached at build time so runs never download them (research R9).
COPY --chown=airflow: src /opt/face_index/src
COPY --chown=airflow: README.md /opt/face_index/
RUN pip install --no-cache-dir --no-deps /opt/face_index
COPY --chown=airflow: docker/warm_models.py /opt/face_index/warm_models.py
RUN python /opt/face_index/warm_models.py

COPY --chown=airflow: dags /opt/airflow/dags
