#!/usr/bin/python3
import argparse
import time
import os
from datetime import datetime
import yaml
import jsonschema
import yfinance as yf
from prometheus_client import start_http_server, Counter, Gauge, Summary, Histogram

# Debug
from pprint import pprint

class finance:

    def __init__(self, args):
        self.print_log("Starting up...")
    # Prepare the config
        self.schema_path = f"{os.path.dirname(__file__)}/schema.yaml"
        self.config = dict()
    # This is hard-coded, see finance.update() quote_info declaration
        self.default_labels     = list(['plugin', 'source', 'ticker'])
        self.load_config(args.config)
    # Set up -- prefer command line arg to yaml arg
        self.verbose            = args.verbose
        self.debug              = args.debug
        self.config['port']     = next(v for v in [ args.port, self.config.get('port') ] if v is not None)
    # Ensure we have sources
        if self.config.get('sources') is None:
            raise Exception('Refusing to initialize with no defined sources')
    # Setup plugins
        self.sources            = self.load_sources()
    # Prep unique list of labels and metrics
        self.labels             = self.load_labels()
        self.metrics            = self.load_metrics()
    # Prepare label cache and pre-populate before first run
        self.label_cache        = dict()
        for ticker in self.config['tickers']:
            self.label_cache[ticker] = { label: None for label in self.labels }
            if self.config['update_cache_on_startup']:
                self.init_cache(ticker)
    # Prepare Prometheus Metrics
        self.prom_metrics               = dict()
        if self.verbose:
            self.print_log(f'Preparing default metrics with labels: {self.labels}')
        self.prom_metrics['updates']    = Counter(f"{self.config['metric_prefix']}_updates", 'Number of ticker updates', self.labels)
        self.prom_metrics['quote_time'] = Gauge(f"{self.config['metric_prefix']}_quote_time", 'Time spent retrieving quote', self.labels)
    # Response time histogram should not include ticker
        histogram_labels = self.default_labels.copy()
        histogram_labels.remove('ticker')
        self.print_log(f"Launching histogram with labels {histogram_labels}")
        self.prom_metrics['quote_histogram'] = Histogram(f"{self.config['metric_prefix']}_quote_histogram",
                                                         'Histogram of quote retrieval times',
                                                         histogram_labels,
                                                         buckets = list(range(1,21)))
    # Initialized Configured Metrics
        self.init_metrics()
        self.print_log("Ready to Run...")

    def load_config(self, config_file):
        with open(config_file, 'r') as config_file:
            self.config = yaml.load(config_file, Loader=yaml.FullLoader)
        with open(self.schema_path) as fd:
            schema = yaml.safe_load(fd)
        self.print_log("Validating Config...")
        try:
            jsonschema.validate(self.config, schema)
        except jsonschema.ValidationError as e:
            path = "/".join(str(item) for item in e.absolute_path)
            raise Exception(f"Invalid config at {path}: {e.message}")

    def print_config(self):
        self.print_log(pprint(self.config))

    def load_sources(self):
        sources = dict()
        for source in self.config['sources']:
            sources[source['name']] = source
            sources[source['name']]['handler'] = yf
        return sources

    def load_labels(self):
        labels = self.default_labels.copy()
        for source in self.config['sources']:
            if source.get('labels') is not None:
                labels = list(set(labels + list(source['labels'].keys())))
        return labels

    def load_metrics(self):
        metrics = dict()
        for source in self.config['sources']:
            # Define source for metric in-case of overlap
            for metric in source['metrics'].keys():
                source['metrics'][metric].update({ 'source': source['name'] })
            metrics.update(source['metrics'])
        return metrics

    def init_cache(self, ticker):
        for source in self.config['sources']:
            if self.verbose:
                self.print_log(f"Prep cache for {source['name']} -> {ticker}")
            quote = self.fetch_data(source, ticker)
            self.quote_labels(source, ticker, quote)

    def init_metrics(self):
        for name, metric in self.metrics.items():
            if self.verbose:
                self.print_log(f"Preparing metric {name}({metric['type']}) from {metric['source']}")
            if metric['type'] == 'Counter':
                self.prom_metrics[name] = Counter(f"{self.config['metric_prefix']}_{name}", metric['help'], self.labels)
            elif metric['type'] == 'Gauge':
                self.prom_metrics[name] = Gauge(f"{self.config['metric_prefix']}_{name}", metric['help'], self.labels)
            elif metric['type'] == 'Histogram':
                self.prom_metrics[name] = Histogram(f"{self.config['metric_prefix']}_{name}", metric['help'], self.labels)
            elif metric['type'] == 'Summary':
                self.prom_metrics[name] = Summary(f"{self.config['metric_prefix']}_{name}", metric['help'], self.labels)

    def start_server(self):
        if self.verbose:
            self.print_log(f"Starting HTTP Server on port {self.config['port']}")
        start_http_server(int(self.config['port']))

    def fetch_data(self, source, ticker):
        handler = source['handler']
        result = None
        try:
            result = handler.Ticker(ticker).info
        except Exception as e:
            self.print_log(f"Unable to fetch {ticker} from {source['name']}")
            if self.verbose:
                self.print_log(e)
        return result

# Prepare labels using standard labels, label cache, and quote labels
    def quote_labels(self, source, ticker, quote):
    # Update label values from Quote
        quote_info = dict({
            'source': source['name'],
            'plugin': source['plugin'],
            'ticker': ticker,
        })
        if source.get('labels') is not None:
            quote_info.update({ label: quote.get(field) for label, field in source.get('labels').items() if quote.get(field) is not None })
    # Update Cache
        self.label_cache[ticker].update(quote_info)
    # Fill in the blanks
        quote_info.update({ label: self.label_cache[ticker][label] for label in self.labels if quote_info.get(label) is None })
        return quote_info

    def update(self, source):
        for ticker in self.config['tickers']:
            if self.verbose:
                self.print_log(f"Updating ticker {ticker} from {source['name']}")
            start_time = time.time()
            quote = dict()
            try:
                quote = self.fetch_data(source, ticker)
                if quote is None:
                    continue
                if self.debug:
                    pprint(quote)
            except Exception as e:
                print(f'Error fetching {ticker}: {e}')
                continue
            duration = time.time() - start_time
        # Handle labels
            quote_info = self.quote_labels(source, ticker, quote)
        # Update Metrics
            if self.debug:
                self.print_log(f'Preparing to load metrics with labels: {quote_info}')
            self.prom_metrics['updates'].labels(**quote_info).inc()
            self.prom_metrics['quote_time'].labels(**quote_info).set(duration)
            self.prom_metrics['quote_histogram'].labels(
                source = source['name'],
                plugin = source['plugin']
            ).observe(duration)
        # Update Configured Metrics
            for name, metric in self.metrics.items():
                if metric['source'] != source['name']:
                    continue
                value = quote.get(metric['item'])
                if value is None:
                    continue
                if metric['type'] == 'Counter':
                    self.prom_metrics[name].labels(**quote_info).inc()
                elif metric['type'] == 'Gauge':
                    self.prom_metrics[name].labels(**quote_info).set(value)
                elif metric['type'] == 'Histogram':
                    self.prom_metrics[name].labels(**quote_info).observe(value)
                elif metric['type'] == 'Summary':
                    self.prom_metrics[name].labels(**quote_info).observe(value)
            if self.verbose:
                self.print_log(f" - Updated {ticker} from {source['name']} in {duration}s")

    def print_log(self, msg):
        print(f'{datetime.now()} {msg}', flush=True)

    def is_market_open(self, d = datetime.today()):
        return d.weekday() < 5 and d.hour > 6 and d.hour < 18

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Google Finance Prometheus Exporter')
    parser.add_argument('-f', '--config', help='Location of config yaml', required=True)
    parser.add_argument('-v', '--verbose', action='store_true', help='Print status to stdout')
    parser.add_argument('-p', '--port', help='Listening port (ip:port or just port)')
    parser.add_argument('-d', '--debug', action="store_true",help="Dump API Data")
    args = parser.parse_args()

# Start up
    f = finance(args)
    if args.debug:
        f.print_log(f'Running with config: ')
        f.print_config()
    f.start_server()

# Track Updates
    last_run = dict()
    for name, source in f.sources.items():
        last_run[name] = 0

# Update in loop
    while True:
        for name, source in f.sources.items():
            if f.is_market_open(datetime.now()) and time.time() - last_run[name] > source['interval']:
                if args.verbose:
                    f.print_log(f"Updating Source {name}")
                f.update(source)
                last_run[name] = time.time()
        time.sleep(f.config['min_interval'])
