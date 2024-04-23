FROM python:3.12.1

COPY action.py create_mkdocs_config.py requirements.txt /src/

RUN python -m pip install --no-cache-dir -r /src/requirements.txt && \
    chmod +x /src/action.py

ENTRYPOINT ["/src/action.py"]

LABEL maintainer="Nicholas Wiltsie, nwiltsie@mednet.ucla.com"
