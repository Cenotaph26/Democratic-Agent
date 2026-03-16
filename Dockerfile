FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs

CMD python main.py --mode=$BOT_MODE --capital=$INITIAL_CAPITAL --scan-interval=$SCAN_INTERVAL --tick-interval=$TICK_INTERVAL
