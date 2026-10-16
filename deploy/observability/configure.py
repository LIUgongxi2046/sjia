"""Render reviewed operational thresholds/receiver settings; never start or send."""
import argparse
import json
import math
from pathlib import Path
import re

import yaml


def positive(value, name, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or value <= 0 or (maximum is not None and value > maximum)):
        raise ValueError(f'INVALID_{name.upper()}')
    return value


def render(config):
    if config.get('operator_approved') is not True or not config.get('approval_reference'):
        raise ValueError('OPERATOR_APPROVAL_REQUIRED')
    delay = config.get('alert_for_seconds')
    positive(delay, 'alert_for_seconds')
    backlog = positive(config.get('outbox_age_seconds'), 'outbox_age_seconds')
    failure = positive(config.get('http_5xx_ratio'), 'http_5xx_ratio', 1)
    tasks = positive(config.get('failed_or_uncertain_tasks'), 'failed_or_uncertain_tasks')
    p95 = positive(config.get('http_p95_seconds'), 'http_p95_seconds')
    target = config.get('scrape_target', '')
    if not re.fullmatch(r'(?:[A-Za-z0-9.-]+):[0-9]{1,5}', target):
        raise ValueError('SCRAPE_TARGET_REQUIRED')
    if not 0 < int(target.rsplit(':', 1)[1]) <= 65535:
        raise ValueError('SCRAPE_TARGET_INVALID')
    webhook_file = config.get('receiver_url_file', '')
    if not isinstance(webhook_file, str) or not webhook_file.startswith('/run/secrets/') or '..' in webhook_file:
        raise ValueError('APPROVED_RECEIVER_FILE_REQUIRED')
    definitions = [
        ('SjiaApiUnavailable', 'up{job="sjia-api"} == 0 or absent(up{job="sjia-api"})', 'API scrape unavailable'),
        ('SjiaOperationalMetricsUnavailable', 'sjia_operational_metrics_available == 0 or absent(sjia_operational_metrics_available)', 'Operational state is unknown'),
        ('SjiaMultiprocessMetricsMisconfigured', 'sjia_metrics_multiprocess != 1', 'API counters are not multiprocess aggregates'),
        ('SjiaOutboxDelayed', f'outbox_oldest_unpublished_seconds > {backlog}', 'Unpublished outbox backlog exceeds the approved budget'),
        ('SjiaHttpFailures', f'sum(rate(api_requests_total{{status=~"5.."}}[5m])) / clamp_min(sum(rate(api_requests_total[5m])), 0.000001) > {failure}', 'HTTP failure ratio exceeds the approved budget'),
        ('SjiaHttpLatency', f'histogram_quantile(0.95, sum by (le) (rate(api_request_duration_seconds_bucket[5m]))) > {p95}', 'HTTP latency exceeds the approved budget'),
        ('SjiaTaskFailures', f'sum(sjia_tasks{{status=~"failed|uncertain|awaiting_resolution"}}) >= {tasks}', 'Failed or uncertain tasks require reconciliation'),
    ]
    rules = {'groups': [{'name': 'sjia-runtime', 'rules': [
        {'alert': name, 'expr': expression, 'for': f'{int(delay)}s',
         'labels': {'severity': 'warning', 'service': 'sjia-runtime'},
         'annotations': {'summary': summary}}
        for name, expression, summary in definitions]}]}
    prometheus = {'global': {'scrape_interval': '15s', 'evaluation_interval': '15s'},
        'rule_files': ['/etc/prometheus/sjia.rules.yaml'],
        'alerting': {'alertmanagers': [{'static_configs': [{'targets': ['alertmanager:9093']}]}]},
        'scrape_configs': [{'job_name': 'sjia-api', 'metrics_path': '/internal/metrics',
            'scrape_timeout': '10s', 'static_configs': [{'targets': [target]}]}]}
    alertmanager = {'route': {'receiver': 'approved-operator', 'group_by': ['alertname', 'service'],
        'group_wait': '30s', 'group_interval': '5m', 'repeat_interval': '4h'},
        'receivers': [{'name': 'approved-operator', 'webhook_configs': [
            {'url_file': webhook_file, 'send_resolved': True, 'max_alerts': 20}]}]}
    return {'sjia.rules.yaml': rules, 'prometheus.yaml': prometheus, 'alertmanager.yaml': alertmanager}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admission', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rendered = render(json.loads(args.admission.read_text()))
    args.output.mkdir(parents=True, exist_ok=False)
    for name, content in rendered.items():
        (args.output / name).write_text(yaml.safe_dump(content, sort_keys=False))
    print('RENDERED_ONLY; NO_SEND; validate with promtool and amtool before approved deployment')


if __name__ == '__main__':
    main()
