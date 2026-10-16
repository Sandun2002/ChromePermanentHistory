"""Conservative, reversible pairing of desktop and automatic Android observations."""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import unicodedata
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit
from cloud_sync import parse_search

WINDOW_US = 2_000_000

def canonical_url(url):
    try:
        parts=urlsplit(url)
        if parts.scheme not in ('http','https') or not parts.hostname: return None
        host=parts.hostname.lower()
        if ':' in host: host='['+host+']'
        if parts.port and (parts.scheme,parts.port) not in (('http',80),('https',443)): host+=':'+str(parts.port)
        if '@' in parts.netloc: host=parts.netloc.rsplit('@',1)[0]+'@'+host
        params=[pair for pair in parse_qsl(parts.query,keep_blank_values=True)
            if not pair[0].lower().startswith('utm_') and pair[0].lower() not in ('gclid','dclid','fbclid','msclkid')]
        params.sort(key=lambda pair:pair[0]) # Stable sorting keeps repeated-parameter value order.
        return urlunsplit((parts.scheme,host,parts.path or '/',urlencode(params),parts.fragment))
    except ValueError: return None

def search_key(url):
    parsed = parse_search(url)
    if not parsed: return None
    query, domain = parsed
    engine = ('google' if domain in {'google.com','www.google.com','google.lk','www.google.lk',
        'google.co.uk','www.google.co.uk','google.co.in','www.google.co.in'} else
        'bing' if domain in {'bing.com','www.bing.com','cn.bing.com'} else
        'duckduckgo' if domain.endswith('duckduckgo.com') else
        'brave' if domain=='search.brave.com' else
        'ecosia' if domain in {'ecosia.org','www.ecosia.org'} else 'yahoo')
    return engine, unicodedata.normalize('NFC',query)

def candidate_pairs(desktop, mobile):
    """Return (desktop id, mobile rowid, delta microseconds, unique-pair flag).

    Inputs use original observation timestamps, never receipt/download times. This is a
    likely-match heuristic, not proof that two visits represent the same action.
    """
    mobile = list(mobile)
    if not mobile: return []
    groups = defaultdict(list)
    for identity,url,stamp in desktop:
        key = search_key(url) or (('url',canonical_url(url)) if canonical_url(url) else None)
        if key and stamp is not None: groups[key].append((int(stamp),identity,url))
    for values in groups.values(): values.sort()
    times = {key:[row[0] for row in values] for key,values in groups.items()}
    candidates = []
    for observation in mobile:
        mobile_id,url,stamp,source = observation[:4]
        full_url = bool(observation[4]) if len(observation)>4 else False
        incognito = len(observation)>5 and observation[5]=='incognito'
        if source!='chrome_android' or stamp is None: continue # Explicit shares may be much later.
        key = search_key(url) or (('url',canonical_url(url)) if full_url and canonical_url(url) else None)
        if key not in groups: continue
        stamp = int(stamp)
        lower,upper = bisect_left(times[key],stamp-WINDOW_US),bisect_right(times[key],stamp+WINDOW_US)
        for observed,desktop_id,desktop_url in groups[key][lower:upper]:
            # Android's historical records omit search mode/pagination metadata. Flag these
            # candidates, but do not group a desktop images/video/filtered-results search.
            params = parse_qs(urlsplit(desktop_url).query)
            special_mode = any(k in params for k in ('tbm','udm','tbs','start','ia','iar','filters'))
            if full_url: special_mode = canonical_url(desktop_url)!=canonical_url(url)
            special_mode = special_mode or incognito
            candidates.append((desktop_id,mobile_id,abs(observed-stamp),special_mode))
    desktop_degree = Counter(row[0] for row in candidates)
    mobile_degree = Counter(row[1] for row in candidates)
    return [(d,m,delta,int(desktop_degree[d]==1 and mobile_degree[m]==1 and not special))
        for d,m,delta,special in candidates]
