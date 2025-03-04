from flask import Flask, jsonify
from flask import render_template
from flask import request
import logging

from email.parser import HeaderParser
from email.header import decode_header

import time
import dateutil.parser

#from datetime import datetime
import datetime
import re

import pygal
from pygal.style import Style

from IPy import IP
import geoip2.database

import argparse

app = Flask(__name__)
reader = geoip2.database.Reader(
    '%s/data/GeoLite2-Country.mmdb' % app.static_folder)


@app.context_processor
def utility_processor():
    def getCountryForIP(line):
        ipv4_address = re.compile(r"""
            \b((?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)\.
            (?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)\.
            (?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)\.
            (?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d))\b""", re.X)
        ip = ipv4_address.findall(line)
        if ip:
            ip = ip[0]  # take the 1st ip and ignore the rest
            if IP(ip).iptype() == 'PUBLIC':
                r = reader.country(ip).country
                if r.iso_code and r.name:
                    return {
                        'iso_code': r.iso_code.lower(),
                        'country_name': r.name
                    }
    return dict(country=getCountryForIP)


@app.context_processor
def utility_processor():
    def duration(seconds, _maxweeks=99999999999):
        return ', '.join(
            '%d %s' % (num, unit)
            for num, unit in zip([
                (seconds // d) % m
                for d, m in (
                    (604800, _maxweeks),
                    (86400, 7), (3600, 24),
                    (60, 60), (1, 60))
            ], ['wk', 'd', 'hr', 'min', 'sec'])
            if num
        )
    return dict(duration=duration)


def dateParser(line):
    try:
        r = dateutil.parser.parse(line, fuzzy=True)

    # if the fuzzy parser failed to parse the line due to
    # incorrect timezone information issue #5 GitHub
    except ValueError:
        r = re.findall('^(.*?)\s*(?:\(|utc)', line, re.I)
        if r:
            r = dateutil.parser.parse(r[0])
    return r


def getHeaderVal(h, data, rex='\s*(.*?)\n\S+:\s'):
    r = re.findall('%s:%s' % (h, rex), data, re.X | re.DOTALL | re.I)
    if r:
        return r[0].strip()
    else:
        return None

def mimeDecode(s):
    if s is None or s == '':
        return ''

    # 各部分をデコードして結合
    decoded_parts = []
    for part, encoding in decode_header(s):
        if isinstance(part, bytes):
            ss = part.decode(encoding or "utf-8")
            decoded_parts.append(ss)
        else:  # すでに文字列の場合（エンコードなし）
            decoded_parts.append(part)

    # 全てを結合
    decoded_text = ''.join(decoded_parts)
    return decoded_text

@app.route('/healthcheck', methods=['GET'])
def healthcheck():
    return jsonify({"status": "healthy"}), 200

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        mail_data = request.form['headers'].strip()
        r = {}
        n = HeaderParser().parsestr(mail_data)
        graph = []
        received = n.get_all('Received')
        if received:
            received = [i for i in received if ('from' in i or 'by' in i)]
        else:
            received = re.findall(
                'Received:\s*(.*?)\n\S+:\s+', mail_data, re.X | re.DOTALL | re.I)
        c = len(received)
        for i in range(len(received)):
            if ';' in received[i]:
                line = received[i].split(';')
            else:
                line = received[i].split('\r\n')
            line = list(map(str.strip, line))
            line = [x.replace('\r\n', ' ') for x in line]
            try:
                if ';' in received[i + 1]:
                    next_line = received[i + 1].split(';')
                else:
                    next_line = received[i + 1].split('\r\n')
                next_line = list(map(str.strip, next_line))
                next_line = [x.replace('\r\n', '') for x in next_line]
            except IndexError:
                next_line = None

            org_time = dateParser(line[-1])
            if not next_line:
                next_time = org_time
            else:
                next_time = dateParser(next_line[-1])

            if line[0].startswith('from'):
                data = re.findall(
                    """
                    from\s+
                    (.*?)\s+
                    by(.*?)
                    (?:
                        (?:with|via)
                        (.*?)
                        (?:\sid\s|$)
                        |\sid\s|$
                    )""", line[0], re.DOTALL | re.X)
            else:
                data = re.findall(
                    """
                    ()by
                    (.*?)
                    (?:
                        (?:with|via)
                        (.*?)
                        (?:\sid\s|$)
                        |\sid\s
                    )""", line[0], re.DOTALL | re.X)

            delay = (org_time - next_time).seconds
            if delay < 0:
                delay = 0

            try:
                # struct_time を datetime に変換（UTC ベース）
                ftime = org_time.utctimetuple()
                ftime_dt = datetime.datetime(*ftime[:6])  # 年, 月, 日, 時, 分, 秒 を渡す
                ftime = time.strftime('%m/%d/%Y %I:%M:%S %p', ftime)

                JST = datetime.timezone(datetime.timedelta(hours=9))
                ftime_jst = ftime_dt.replace(tzinfo=datetime.timezone.utc).astimezone(JST)
                ftime2 = ftime_jst.strftime('%Y-%m-%d %H:%M:%S%z')
                ftime = org_time.utctimetuple()
                ftime = time.strftime('%m/%d/%Y %I:%M:%S %p', ftime)
                r[c] = {
                    'Timestmp': org_time,
                    'Time': ftime,
                    'Time2': ftime2,
                    'Delay': delay,
                    'Direction': [x.replace('\n', ' ') for x in list(map(str.strip, data[0]))]
                }
                c -= 1
            except IndexError:
                pass

        for i in list(r.values()):
            if i['Direction'][0]:
                graph.append(["From: %s" % i['Direction'][0], i['Delay']])
            else:
                graph.append(["By: %s" % i['Direction'][1], i['Delay']])

        totalDelay = sum([x['Delay'] for x in list(r.values())])
        fTotalDelay = utility_processor()['duration'](totalDelay)
        delayed = True if totalDelay else False

        custom_style = Style(
            background='transparent',
            plot_background='transparent',
            font_family='googlefont:Open Sans',
            # title_font_size=12,
        )
        line_chart = pygal.HorizontalBar(
            style=custom_style, height=250, legend_at_bottom=True,
            tooltip_border_radius=10)
        line_chart.tooltip_fancy_mode = False
        line_chart.title = 'Total Delay is: %s' % fTotalDelay
        line_chart.x_title = 'Delay in seconds.'
        for i in graph:
            line_chart.add(i[0], i[1])
        chart = line_chart.render(is_unicode=True)

        summary = {
            'From': mimeDecode(n.get('From')) or getHeaderVal('from', mail_data),
            'To': mimeDecode(n.get('to')) or getHeaderVal('to', mail_data),
            'Cc': mimeDecode(n.get('cc')), # or getHeaderVal('cc', mail_data),
            'Subject': mimeDecode(n.get('Subject')) or  getHeaderVal('Subject', mail_data),
            'MessageID': n.get('Message-ID') or getHeaderVal('Message-ID', mail_data),
            'Date': n.get('Date') or getHeaderVal('Date', mail_data),
        }

        security_headers = ['Received-SPF', 'Authentication-Results',
                            'DKIM-Signature', 'ARC-Authentication-Results']
        sh = {}
        for k,v in n.items():
            if k in security_headers:
                pass_patterns = [r'dkim=pass', r'dmarc=pass', r'spf=pass']
                v2 = v
                for pattern in pass_patterns:
                    v2 = re.sub(pattern, rf'<span class="badge badge-success">{pattern}</span>', v2)

                fail_patterns = [r'dkim=fail', r'dmarc=fail', r'spf=fail']
                for pattern in fail_patterns:
                    v2 = re.sub(pattern, rf'<span class="badge badge-danger">{pattern}</span>', v2)

                softfail_patterns = [r'spf=softfail']
                for pattern in softfail_patterns:
                    v2 = re.sub(pattern, rf'<span class="badge badge-warning">{pattern}</span>', v2)

                none_patterns = [r'dkim=neutral', r'dmarc=none', r'dmarc=bestguesspass', r'spf=none']
                for pattern in none_patterns:
                    v2 = re.sub(pattern, rf'<span class="badge badge-dark">{pattern}</span>', v2)

                sh[k] = v2

        return render_template(
            'index.html', data=r, delayed=delayed, summary=summary,
            n=n, chart=chart, sh=sh)
    else:
        return render_template('index.html')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Mail Header Analyser")
    parser.add_argument("-d", "--debug", action="store_true", default=False,
                        help="Enable debug mode")
    parser.add_argument("-b", "--bind", default="127.0.0.1", type=str)
    parser.add_argument("-p", "--port", default="8080", type=int)
    args = parser.parse_args()

    app.debug = args.debug
    app.run(host=args.bind, port=args.port)
