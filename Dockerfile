FROM python:3.6-slim

WORKDIR /usr/src/w3act

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python setup.py install

CMD w3act


