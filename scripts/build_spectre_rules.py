"""Compile the Stash routing policy into a self-contained Spectre rule list.

Requires Python 3, PyYAML and curl. Upstream URLs and checksums are locked in
configs/spectre-sources.json. No proxy credentials or device API are needed.
"""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = {'DOMAIN', 'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'IP-CIDR',
             'IP-CIDR6', 'GEOIP', 'FINAL'}


def policy(name):
    if name in ('DIRECT', 'REJECT', 'PROXY'):
        return name
    if name == '黑名单 · 广告拦截':
        return 'REJECT'
    if name == '黑名单 · 漏网之鱼':
        return 'DIRECT'
    if name.startswith('黑名单 · '):
        return 'PROXY'
    raise ValueError(f'Unknown policy: {name}')


def domain_rule(value, target):
    if value.startswith('+.'):
        kind, value = 'DOMAIN-SUFFIX', value[2:]
    else:
        kind = 'DOMAIN'
    if not value or any(x in value for x in '*+, \n\r') or value.startswith('.'):
        raise ValueError(f'Unsupported domain pattern: {value}')
    return f'{kind},{value},{target}'


def convert(value, target, skipped, provider):
    parts = [p.strip() for p in value.split(',')]
    kind, payload = parts[:2]
    if kind in ('IP-ASN', 'PROCESS-NAME'):
        skipped.append({'provider': provider, 'rule': value,
                        'reason': 'Not emitted by the conservative Spectre text profile'})
        return None
    if kind not in SUPPORTED - {'FINAL'}:
        raise ValueError(f'Unsupported rule: {value}')
    if kind in ('IP-CIDR', 'IP-CIDR6'):
        net = ipaddress.ip_network(payload, strict=False)
        kind = 'IP-CIDR6' if net.version == 6 else 'IP-CIDR'
        payload = str(net)
    # Only the basic TYPE,VALUE,POLICY syntax is emitted. no-resolve is not
    # required for route selection, but omitting it may change DNS timing.
    return f'{kind},{payload},{target}'


def compile_rules(cache):
    source = ROOT / 'configs/stash-blacklist-routing.stoverride'
    cfg = yaml.safe_load(source.read_text())
    sources = json.loads((ROOT / 'configs/spectre-sources.json').read_text())
    cache.mkdir(parents=True, exist_ok=True)
    providers = {}
    for name, info in sources.items():
        dest = cache / (name + '.yaml')
        if not dest.exists():
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as tmp:
                temp = Path(tmp.name)
            try:
                subprocess.run(['curl', '-fsSL', '--max-time', '60', '--retry', '2',
                                info['url'], '-o', str(temp)], check=True)
                if hashlib.sha256(temp.read_bytes()).hexdigest() != info['sha256']:
                    raise ValueError(f'Checksum mismatch: {name}')
                temp.replace(dest)
            finally:
                temp.unlink(missing_ok=True)
        data = dest.read_bytes()
        if hashlib.sha256(data).hexdigest() != info['sha256']:
            raise ValueError(f'Cached checksum mismatch: {name}')
        providers[name] = yaml.safe_load(data)['payload']
        if len(providers[name]) != info['entries']:
            raise ValueError(f'Entry count mismatch: {name}')

    rules, seen, skipped = [], set(), []

    def emit(rule):
        if rule and rule not in seen:
            seen.add(rule)
            rules.append(rule)

    for original in cfg['rules']:
        parts = original.split(',')
        kind = parts[0]
        if kind == 'MATCH':
            emit('FINAL,' + policy(parts[1]))
        elif kind == 'RULE-SET':
            name, target = parts[1], policy(parts[2])
            behavior = cfg['rule-providers'][name]['behavior']
            if behavior != sources[name]['behavior']:
                raise ValueError(f'Provider behavior changed: {name}')
            for entry in providers[name]:
                if behavior == 'domain':
                    emit(domain_rule(entry, target))
                elif behavior == 'ipcidr':
                    emit(convert('IP-CIDR,' + entry, target, skipped, name))
                elif behavior == 'classical':
                    emit(convert(entry, target, skipped, name))
                else:
                    raise ValueError(f'Unknown behavior: {behavior}')
        else:
            emit(convert(original, policy(parts[2]), skipped, 'local'))

    header = [
        '# Spectre VPN - domestic apps DIRECT / blacklist PROXY',
        '# Version: ' + str(cfg['version']),
        '# Generated from stash-blacklist-routing.stoverride; do not edit.',
        '# Import as a rule URL, not a server subscription or sing-box JSON.',
        '# PROXY uses the server selected in Spectre. Unmatched traffic: DIRECT.',
        '# Static conversion validated; Spectre device import is not yet verified.',
        '# Pinned upstream sources and limitations: spectre-sources.json / SPECTRE.md',
        '', '[Rule]',
    ]
    output = '\n'.join(header + rules) + '\n'
    validate(rules, cfg)
    manifest = {
        'version': cfg['version'], 'rules': len(rules),
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'output_sha256': hashlib.sha256(output.encode()).hexdigest(),
        'output_bytes': len(output.encode()), 'final_policy': 'DIRECT',
        'skipped_rules': skipped, 'device_import_verified': False,
        'notes': ['no-resolve options omitted', 'Exact duplicate rules removed',
                  'Named proxy groups mapped to PROXY; no node-selection groups',
                  'Advert blocking fixed to REJECT; fallback fixed to DIRECT'],
    }
    return output, json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'


def validate(rules, cfg):
    if rules[-1] != 'FINAL,DIRECT' or sum(r.startswith('FINAL,') for r in rules) != 1:
        raise ValueError('Invalid final rule')
    for rule in rules:
        parts = rule.split(',')
        if parts[0] not in SUPPORTED or parts[-1] not in ('DIRECT', 'PROXY', 'REJECT'):
            raise ValueError(f'Invalid output: {rule}')
        if len(parts) != (2 if parts[0] == 'FINAL' else 3):
            raise ValueError(f'Invalid field count: {rule}')

    def match(host):
        for rule in rules:
            p = rule.split(',')
            if p[0] == 'DOMAIN' and host == p[1]: return p[2]
            if p[0] == 'DOMAIN-SUFFIX' and (host == p[1] or host.endswith('.' + p[1])): return p[2]
            if p[0] == 'DOMAIN-KEYWORD' and p[1] in host: return p[2]
            if p[0] == 'FINAL': return p[1]

    # Protect every high-priority application exception, including subdomains.
    end = cfg['rules'].index('DOMAIN,t.nyxbits.com,黑名单 · 代理模式')
    for rule in cfg['rules'][10:end]:
        p = rule.split(',')
        assert p[0] == 'DOMAIN-SUFFIX' and p[2] == 'DIRECT'
        for host in (p[1], 'test.' + p[1]):
            if match(host) != 'DIRECT': raise ValueError(f'Direct regression: {host}')
    for host in ('chatgpt.com', 'api.openai.com', 'claude.ai', 'www.google.com',
                 'www.youtube.com', 'github.com', 'api.telegram.org', 'www.netflix.com'):
        if match(host) != 'PROXY': raise ValueError(f'Proxy regression: {host}')
    # Unknown hosts remain direct in the blacklist profile.
    if match('spectre-unmatched-test.invalid') != 'DIRECT':
        raise ValueError('Fallback regression')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path,
                        default=Path(tempfile.gettempdir()) / 'gzh-spectre-providers')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    output, report = compile_rules(args.cache_dir)
    paths = [(ROOT / 'configs/spectre-blacklist-routing.conf', output),
             (ROOT / 'configs/spectre-validation.json', report)]
    for path, text in paths:
        if args.check:
            if path.read_text() != text: raise SystemExit(f'Generated file is stale: {path}')
        else:
            path.write_text(text)
    print(report)


if __name__ == '__main__':
    main()
