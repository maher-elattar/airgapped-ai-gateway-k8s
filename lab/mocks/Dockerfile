FROM docker.io/library/python@sha256:285a71327884a4d50efbea30104473b0fa43ecefa499458899670ca30dae76e5

WORKDIR /app
COPY openai_mock.py /app/openai_mock.py

USER 65532:65532
EXPOSE 8000
ENTRYPOINT ["python", "/app/openai_mock.py"]
