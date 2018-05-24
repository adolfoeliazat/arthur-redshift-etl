FROM python:3
COPY requirements.txt ./
COPY requirements-dev.txt ./
RUN pip install --requirement ./requirements-dev.txt
COPY . .
RUN [ "python", "setup.py", "develop" ]
ENTRYPOINT ["arthur.py"]
