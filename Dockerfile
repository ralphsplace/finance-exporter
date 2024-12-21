FROM python:3.13
RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app
COPY requirements.txt ./
RUN apt-get update && apt-get install -y 
RUN pip3 install --no-cache-dir -r requirements.txt
ENV CONFIG '/usr/src/app/finance-exporter.yaml'
ENV OPTS ''
COPY schema.yaml ./
COPY finance-exporter.py ./
CMD ["sh", "-c", "python3 finance-exporter.py -f ${CONFIG} ${OPTS}"]
