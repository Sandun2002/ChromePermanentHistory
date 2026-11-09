#!/usr/bin/env python3
"""
Private local analytics dashboard for the permanent Chrome history archive.

The server binds only to 127.0.0.1. Archive deletion is available from the dashboard.
"""

from __future__ import annotations

import json
import csv
import ctypes
import hashlib
import html
import io
import ipaddress
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time as time_module
from collections import Counter, defaultdict
from datetime import date, datetime, time as clock_time, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from http.cookies import SimpleCookie, CookieError
from contextlib import contextmanager, closing
from cloud_sync import Bridge, SyncError, parse_search
from search_duplicates import candidate_pairs


ARCHIVE_DIRECTORY = Path(__file__).resolve().parent
ARCHIVE_DATABASE = ARCHIVE_DIRECTORY / 'PermanentChromeHistory.sqlite'
NSFW_RULES_FILE = ARCHIVE_DIRECTORY / 'nsfw_rules.json'
THUMBNAIL_CACHE = ARCHIVE_DIRECTORY / 'thumbnail_cache'
COLLECTOR_LOG = ARCHIVE_DIRECTORY / 'collector.log'
COLLECTOR_SCRIPT = ARCHIVE_DIRECTORY / 'history_collector.py'
COLLECTOR_PAUSE_REQUEST = ARCHIVE_DIRECTORY / ".collector-paused"
COLLECTOR_SCAN_REQUEST = ARCHIVE_DIRECTORY / ".collector-scan-now"
COLLECTOR_STOP_REQUEST = ARCHIVE_DIRECTORY / ".collector-stop"
STARTUP_LAUNCHER = Path(
    r"C:\Users\sandu\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\StartChromePermanentHistoryProfile13.vbs"
)
COLLECTOR_MUTEX_NAME = r"Local\ChromePermanentHistory-Profile13"
HOST = "127.0.0.1"
PORT = 8765
SRI_LANKA_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="Asia/Colombo")
MAX_PAGE_SIZE = 500
SEARCH_QUERY_KEYS = ("q", "query", "search_query", "p", "text", "k", "_nkw", "search", "keyword", "term", "s")
SHARED_BRIDGE = None
ADULT_SEARCH_TERMS = (
    "porn",
    "pornhub",
    "pronhub",
    "pornography",
    "xxx",
    "nude",
    "nudes",
    "nudity",
    "hentai",
    "onlyfans",
    "camgirl",
    "cam girls",
    "adult video",
    "sex video",
    "sex videos",
    "erotic",
    "fetish",
    "blowjob",
    "masturbation",
    "escort",
    "nsfw",
    "milf",
    "anal",
    "pussy",
    "fucking",
    "fuck",
    "cumshot",
    "cumshots",
    "rule 34",
    "rule34",
    "jav",
    "bdsm",
    "threesome",
    "lesbian porn",
    "gay porn",
    "amateur porn",
)
NSFW_SEARCH_CATEGORIES = (
    ("hentai_anime", "Hentai & anime", ("hentai", "rule 34", "rule34", "jav")),
    ("nudes", "Nudity & nudes", ("nudes", "nude", "nudity")),
    ("fetish_bdsm", "Fetish & BDSM", ("fetish", "bdsm")),
    ("cams_creators", "Cams & creators", ("camgirl", "cam girls", "onlyfans", "fansly", "manyvids")),
    ("escorts", "Escorts", ("escort",)),
    ("explicit_acts", "Explicit acts", ("blowjob", "masturbation", "anal", "pussy", "fucking", "fuck", "threesome", "lesbian porn", "gay porn")),
    ("pornography", "Pornography", ("pornography", "porn", "xxx", "adult video", "sex video", "cumshot", "cumshots", "amateur porn", "milf")),
)
ADULT_DOMAIN_MARKERS = (
    "pornhub",
    "xvideos",
    "xnxx",
    "xhamster",
    "youporn",
    "redtube",
    "spankbang",
    "chaturbate",
    "stripchat",
    "bongacams",
    "livejasmin",
    "brazzers",
    "realitykings",
    "onlyfans",
    "fansly",
    "manyvids",
    "faphouse",
    "motherless",
    "rule34",
    "rule34video",
)
ADULT_DIRECT_TERMS = (
    "porn",
    "xxx",
    "hentai",
    "onlyfans",
    "nsfw",
    "camgirl",
    "camgirls",
    "adult-video",
    "adult video",
    "sex-video",
    "sex video",
    "webcam-sex",
    "webcam sex",
    "cumshot",
)
NOISE_DOMAIN_MARKERS = (
    "accounts.google.com",
    "consent.google.",
    "googleadservices.com",
    "doubleclick.net",
    "googlesyndication.com",
)
NOISE_URL_MARKERS = (
    "google.com/url?",
    "google.com/aclk?",
    "about:blank",
    "chrome://",
)
NSFW_RULE_KEYS = ("include_domains", "include_terms", "ignore_domains", "ignore_terms")
NSFW_STOP_WORDS = {"a", "an", "and", "for", "how", "in", "is", "of", "on", "the", "to", "what", "with"}


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>History Archive Analytics</title>
  <style>
    :root {
      --ink: #102236;
      --muted: #667588;
      --line: #dce5ed;
      --canvas: #f4f7fa;
      --panel: #ffffff;
      --accent: #1368ce;
      --accent-soft: #e8f1fd;
      --teal: #00a69a;
      --shadow: 0 12px 30px rgba(31, 55, 82, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: var(--canvas);
      font-family: Inter, Aptos, Segoe UI, Arial, sans-serif;
    }
    .shell { width: min(1440px, calc(100% - 40px)); margin: 0 auto; }
    header {
      background: linear-gradient(120deg, #0d315e, #126bc9);
      color: #fff;
      padding: 34px 0 31px;
      box-shadow: 0 5px 18px rgba(9, 39, 76, .16);
    }
    .eyebrow {
      margin: 0 0 8px;
      color: #a8d2ff;
      font-size: 12px;
      letter-spacing: .12em;
      font-weight: 800;
      text-transform: uppercase;
    }
    h1 { margin: 0; font-size: clamp(27px, 4vw, 42px); letter-spacing: -.035em; }
    .subhead { margin: 10px 0 0; color: #d9ebff; font-size: 15px; }
    main { padding: 28px 0 46px; }
    .status {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 20px;
      color: var(--muted);
      font-size: 13px;
    }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--teal); }
    .collector-card {
      display: grid;
      grid-template-columns: minmax(190px, .75fr) repeat(3, minmax(0, 1fr)) auto;
      gap: 14px;
      align-items: center;
      margin: 0 0 20px;
      padding: 16px 18px;
      border: 1px solid #cfe0ef;
      border-radius: 15px;
      background: #fff;
      box-shadow: var(--shadow);
    }
    .collector-title { display: grid; gap: 4px; }
    .collector-title small, .collector-fact small { color: var(--muted); font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; }
    .collector-state { display: flex; align-items: center; gap: 8px; font-size: 17px; font-weight: 850; }
    .collector-dot { width: 10px; height: 10px; border-radius: 50%; background: #d78c30; }
    .collector-dot.healthy { background: var(--teal); }
    .collector-dot.warning { background: #d78c30; }
    .collector-fact { min-width: 0; }
    .collector-fact b { display: block; margin-top: 4px; overflow: hidden; color: var(--ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
    .filters, .panel, .stat {
      background: var(--panel);
      border: 1px solid rgba(207, 219, 229, .95);
      box-shadow: var(--shadow);
    }
    .filters {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) repeat(4, 130px) auto;
      gap: 12px;
      padding: 16px;
      border-radius: 16px;
      align-items: end;
    }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 12px; font-weight: 700; }
    input {
      min-width: 0;
      height: 39px;
      padding: 0 11px;
      border: 1px solid #cbd7e3;
      border-radius: 9px;
      color: var(--ink);
      background: #fff;
      font: inherit;
    }
    input:focus { outline: 3px solid #cce3ff; border-color: var(--accent); }
    .actions { display: flex; gap: 8px; }
    button {
      height: 39px;
      border: 0;
      border-radius: 9px;
      padding: 0 14px;
      color: #fff;
      background: var(--accent);
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }
    button:hover { filter: brightness(.95); }
    button.ghost { color: var(--accent); background: var(--accent-soft); }
    .quick {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 13px 0 23px;
    }
    .quick button { height: 30px; padding: 0 11px; color: #3e5a75; background: #e7edf4; font-size: 12px; }
    .stat-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 15px;
      margin-bottom: 16px;
    }
    .stat { border-radius: 15px; padding: 18px; }
    .stat p { margin: 0; color: var(--muted); font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: .07em; }
    .stat strong { display: block; margin-top: 7px; font-size: clamp(27px, 3vw, 37px); letter-spacing: -.04em; }
    .stat span { display: block; margin-top: 6px; color: var(--muted); font-size: 12px; }
    .layout { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(290px, .85fr); gap: 16px; }
    .insight-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(290px, 1fr); gap: 16px; margin-top: 16px; align-items: start; }
    .panel { min-width: 0; border-radius: 16px; padding: 20px; }
    .panel h2 { margin: 0; font-size: 16px; letter-spacing: -.01em; }
    .panel .hint { margin: 5px 0 17px; color: var(--muted); font-size: 12px; }
    .bars {
      display: flex;
      align-items: end;
      gap: 5px;
      min-height: 202px;
      padding: 14px 0 0;
      border-bottom: 1px solid var(--line);
    }
    .bar-wrap { min-width: 0; flex: 1; display: grid; align-items: end; gap: 7px; }
    .bar {
      min-height: 3px;
      border-radius: 5px 5px 0 0;
      background: linear-gradient(180deg, #3f9bf3, #1268cb);
      cursor: default;
    }
    .bar-label { overflow: hidden; color: var(--muted); font-size: 10px; text-align: center; white-space: nowrap; text-overflow: ellipsis; }
    .domain-list { display: grid; gap: 12px; }
    .domain-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; }
    .domain-row div { min-width: 0; }
    .domain-row b { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
    .track { height: 6px; margin-top: 6px; overflow: hidden; border-radius: 99px; background: #e5edf5; }
    .fill { height: 100%; border-radius: 99px; background: var(--teal); }
    .domain-row span { color: var(--muted); font-size: 12px; font-weight: 800; }
    .query-list { display: grid; gap: 9px; }
    .query-row { border-bottom: 1px solid var(--line); }
    .query-row:last-child { border-bottom: 0; }
    .query-link { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; padding: 10px 0; color: inherit; text-decoration: none; }
    .query-link:hover b { color: var(--accent); text-decoration: underline; }
    .query-row b { display: block; overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
    .query-row small { display: block; margin-top: 3px; overflow: hidden; color: var(--muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
    .query-row span { align-self: center; color: var(--accent); font-size: 13px; font-weight: 800; }
    .hour-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 8px; }
    .hour-card { display: grid; grid-template-rows: 17px 82px 16px; gap: 4px; min-width: 0; padding: 7px 4px 5px; border: 1px solid #e1e9f0; border-radius: 9px; background: #f8fbfd; }
    .hour-count { overflow: hidden; color: #46647e; font-size: 11px; font-weight: 800; line-height: 17px; text-align: center; text-overflow: ellipsis; white-space: nowrap; }
    .hour-meter { display: flex; align-items: end; justify-content: center; border-bottom: 1px solid #b8cbe0; }
    .hour-value { width: min(28px, 78%); min-height: 3px; border-radius: 5px 5px 0 0; background: linear-gradient(180deg, #45a1f4, #1268cb); }
    .hour-label { overflow: hidden; color: #405e78; font-size: 10px; font-weight: 800; line-height: 16px; text-align: center; text-overflow: ellipsis; white-space: nowrap; }
    .adult-summary { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 0 0 13px; }
    .adult-stat { padding: 11px; border: 1px solid #f1d4c5; border-radius: 10px; background: #fff8f4; }
    .adult-stat span { display: block; color: #88604d; font-size: 11px; font-weight: 800; text-transform: uppercase; }
    .adult-stat b { display: block; margin-top: 4px; color: #9a3d16; font-size: 24px; }
    #nsfw-intelligence { padding:22px; }
    .nsfw-stat-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); margin:2px 0 18px; border:1px solid var(--line); border-radius:12px; overflow:hidden; }
    .nsfw-stat { min-width:0; padding:13px 15px; background:var(--panel); border-right:1px solid var(--line); }
    .nsfw-stat:last-child { border-right:0; }
    .nsfw-stat span { display:block; overflow:hidden; color:var(--muted); font-size:10px; font-weight:800; letter-spacing:.07em; text-overflow:ellipsis; text-transform:uppercase; white-space:nowrap; }
    .nsfw-stat b { display:block; font-size:22px; line-height:1.15; margin-top:5px; }
    .nsfw-primary-grid,.nsfw-grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:18px; }
    .nsfw-browsing-time { margin:2px 0 18px; padding:18px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
    .nsfw-browsing-time .panel-head { margin-bottom:12px; }
    .nsfw-browsing-time .time-summary { margin-bottom:14px; }
    .nsfw-search-taxonomy { margin:2px 0 18px; padding:18px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
    .nsfw-search-taxonomy-head { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:12px; }
    .nsfw-category-grid { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px; }
    .nsfw-category-card { display:flex; align-items:baseline; gap:7px; min-width:0; min-height:0; padding:8px 10px; border:1px solid var(--line); border-radius:999px; color:var(--ink); background:var(--input); text-align:left; font-family:inherit; cursor:pointer; }
    .nsfw-category-card:hover,.nsfw-category-card.active { border-color:var(--accent); background:var(--accent-soft); }
    .nsfw-category-card b,.nsfw-category-card small,.nsfw-category-card span { display:block; }
    .nsfw-category-card b { overflow:hidden; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
    .nsfw-category-card span { margin-top:0; color:var(--accent); font-size:13px; font-weight:750; letter-spacing:0; }
    .nsfw-category-card small { display:none; }
    .nsfw-source-filters { display:flex; flex-wrap:wrap; gap:6px; margin:0 0 12px; }
    .nsfw-source-filters button { height:30px; padding:0 9px; border:1px solid var(--line); border-radius:999px; color:var(--muted); background:var(--panel); font-size:11px; }
    .nsfw-source-filters button.active { border-color:var(--accent); color:var(--accent); background:var(--accent-soft); }
    .nsfw-keyword-drilldown { max-height:480px; margin-top:12px; overflow:auto; padding:14px; border:1px solid var(--line); border-radius:11px; background:var(--panel-soft); }
    .nsfw-keyword-head { display:flex; justify-content:space-between; gap:12px; align-items:baseline; }
    .nsfw-keyword-head h3 { margin:0; }
    .nsfw-keyword-head span { color:var(--muted); font-size:11px; }
    .nsfw-keyword-list { margin-top:8px; }
    .nsfw-keyword-list .query-link { padding:9px 0; }
    .nsfw-keyword-section + .nsfw-keyword-section { margin-top:18px; padding-top:16px; border-top:1px solid var(--line); }
    .nsfw-query-sources { display:block; overflow:hidden; margin-top:4px; color:var(--muted); font-size:10px; line-height:1.4; text-overflow:ellipsis; white-space:nowrap; }
    .nsfw-more[open] .nsfw-more-content { padding:0 0 18px; }
    .nsfw-search-explorer { margin:2px 0 18px; padding:18px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
    .nsfw-explorer-head { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; margin-bottom:12px; }
    .nsfw-explorer-toolbar { display:grid; grid-template-columns:auto auto minmax(150px,1fr); gap:10px; align-items:center; margin-bottom:10px; }
    .nsfw-explorer-tabs,.nsfw-source-filters { display:flex; flex-wrap:wrap; gap:5px; margin:0; }
    .nsfw-explorer-tabs button,.nsfw-source-filters button { height:32px; padding:0 10px; border:1px solid var(--line); border-radius:999px; color:var(--muted); background:var(--panel); font-size:11px; }
    .nsfw-explorer-tabs button.active,.nsfw-source-filters button.active { border-color:var(--accent); color:var(--accent); background:var(--accent-soft); }
    #nsfw-explorer-filter { width:100%; height:34px; }
    .nsfw-explorer-results { max-height:480px; overflow:auto; border:1px solid var(--line); border-radius:11px; background:var(--panel-soft); }
    .nsfw-explorer-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:center; padding:12px 14px; border-bottom:1px solid var(--line); }
    .nsfw-explorer-row:last-child { border-bottom:0; }
    .nsfw-explorer-row a { color:var(--ink); text-decoration:none; }
    .nsfw-explorer-row a:hover b { color:var(--accent); text-decoration:underline; }
    .nsfw-explorer-row b,.nsfw-explorer-row small { display:block; }
    .nsfw-explorer-row b { font-size:13px; line-height:1.35; }
    .nsfw-explorer-row small { margin-top:4px; color:var(--muted); font-size:10px; line-height:1.45; }
    .nsfw-explorer-count { color:var(--accent); font-size:12px; font-weight:750; white-space:nowrap; }
    .nsfw-related-phrases { margin-top:5px; color:var(--muted); font-size:10px; line-height:1.45; }
    @media (max-width:760px) { .nsfw-explorer-toolbar { grid-template-columns:1fr; } }
    .nsfw-primary-card { min-width:0; padding:15px; border:1px solid var(--line); border-radius:12px; background:var(--panel-soft); }
    #nsfw-intelligence h3 { margin:0; font-size:14px; } #nsfw-intelligence h3 + .hint { margin:5px 0 12px; }
    .nsfw-timeline { display:flex; align-items:end; gap:3px; min-height:112px; padding-top:8px; }
    .nsfw-timeline span { flex:1; min-width:3px; background:var(--accent); border-radius:3px 3px 1px 1px; }
    .nsfw-heatmap { display:grid; grid-template-columns:28px repeat(24, minmax(7px,1fr)); gap:3px; align-items:center; overflow:auto; }
    .nsfw-heatmap b { font-size:10px; color:var(--muted); }
    .nsfw-cell { height:15px; border-radius:3px; background:var(--panel-soft); }
    .nsfw-cell[data-level="1"] { background:#b8d7ff; } .nsfw-cell[data-level="2"] { background:#6aa7f5; } .nsfw-cell[data-level="3"] { background:var(--accent); }
    .nsfw-details { margin-top:12px; border-top:1px solid var(--line); }
    .nsfw-details summary { padding:15px 0; cursor:pointer; color:var(--ink); font-size:13px; font-weight:750; list-style:none; }
    .nsfw-details summary::-webkit-details-marker { display:none; }
    .nsfw-details summary::after { content:'+'; float:right; color:var(--muted); font-size:18px; font-weight:400; line-height:12px; }
    .nsfw-details[open] summary::after { content:'−'; }
    .nsfw-details[open] .nsfw-grid { padding:0 0 18px; }
    .compact-reasons { margin-top:12px; }
    .nsfw-rule-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; padding-bottom:12px; }
    .nsfw-rule-grid label { color:var(--muted); font-size:12px; font-weight:700; }
    .nsfw-rule-grid textarea { box-sizing:border-box; width:100%; min-height:72px; margin-top:5px; resize:vertical; border:1px solid var(--line); border-radius:9px; padding:9px; background:var(--panel); color:var(--ink); font:inherit; }
    .nsfw-rule-actions { display:flex; align-items:center; gap:12px; } .nsfw-rule-actions .hint { margin:0; }
    .nsfw-session { padding:9px 0; border-bottom:1px solid var(--line); } .nsfw-session:last-child { border-bottom:0; }
    @media (max-width: 760px) { #nsfw-intelligence { padding:16px; } .nsfw-stat-grid { grid-template-columns:1fr 1fr; } .nsfw-stat:nth-child(2) { border-right:0; } .nsfw-stat:nth-child(-n+2) { border-bottom:1px solid var(--line); } .nsfw-primary-grid,.nsfw-grid,.nsfw-rule-grid { grid-template-columns:1fr; } }
    .compact-list { display: grid; gap: 11px; }
    .visits { margin-top: 16px; }
    .table-head { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 12px; }
    .table-head p { margin: 3px 0 0; color: var(--muted); font-size: 12px; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 11px; }
    table { width: 100%; min-width: 760px; border-collapse: collapse; }
    th { padding: 11px 13px; color: #5d6f81; background: #f5f8fb; font-size: 11px; text-align: left; letter-spacing: .06em; text-transform: uppercase; }
    td { padding: 12px 13px; border-top: 1px solid var(--line); font-size: 13px; vertical-align: top; }
    td.time { width: 165px; color: var(--muted); white-space: nowrap; font-size: 12px; }
    td.domain { width: 160px; color: #45617a; }
    .visit-title { display: block; max-width: 520px; overflow: hidden; color: var(--ink); font-weight: 750; text-overflow: ellipsis; white-space: nowrap; }
    .visit-url { display: block; max-width: 520px; margin-top: 3px; overflow: hidden; color: #688097; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
    .visit-link { display: block; color: inherit; text-decoration: none; }
    .visit-link:hover .visit-title { color: var(--accent); text-decoration: underline; }
    .empty { padding: 38px; color: var(--muted); text-align: center; }
    .pager { display: flex; justify-content: space-between; align-items: center; margin-top: 14px; }
    .pager button:disabled { cursor: not-allowed; opacity: .45; }
    .pager span { color: var(--muted); font-size: 13px; }
    .privacy { margin: 20px 0 0; color: #60758a; font-size: 12px; text-align: center; }
    @media (max-width: 900px) {
      .filters { grid-template-columns: 1fr 1fr; }
      .filters label:first-child { grid-column: 1 / -1; }
      .layout { grid-template-columns: 1fr; }
      .stat-grid { grid-template-columns: repeat(2, 1fr); }
      .insight-grid { grid-template-columns: 1fr; }
      .hour-grid { grid-template-columns: repeat(8, minmax(0, 1fr)); }
      .collector-card { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 540px) {
      .shell { width: min(100% - 24px, 1440px); }
      header { padding: 27px 0; }
      main { padding-top: 18px; }
      .filters { grid-template-columns: 1fr; }
      .filters label:first-child { grid-column: auto; }
      .actions { display: grid; grid-template-columns: 1fr 1fr; }
      .stat-grid { gap: 10px; }
      .panel { padding: 15px; }
      .hour-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .collector-card { grid-template-columns: 1fr; }
    }

    :root {
      --ink: #181816;
      --muted: #6d6a66;
      --line: #e5e1dc;
      --canvas: #f4f2ee;
      --panel: #ffffff;
      --accent: #1d4ed8;
      --accent-soft: #e8efff;
      --teal: #137c6a;
      --shadow: none;
      --header-bg: #171716;
      --header-text: #fffdf9;
      --input: #fcfbf9;
    }
    :root[data-theme="black"] {
      --ink: #f5f2eb;
      --muted: #aaa7a0;
      --line: #2a2a28;
      --canvas: #000000;
      --panel: #0b0b0a;
      --accent: #c8ff2f;
      --accent-soft: #192000;
      --teal: #57dcbc;
      --shadow: none;
      --header-bg: #000000;
      --header-text: #f7f5ef;
      --input: #11110f;
    }
    body { font-family: "Segoe UI Variable", "Aptos", "Helvetica Neue", Arial, sans-serif; background: var(--canvas); color: var(--ink); }
    .shell { width: min(1400px, calc(100% - 48px)); }
    header { background: var(--header-bg); color: var(--header-text); padding: 25px 0 24px; box-shadow: none; border-bottom: 1px solid #33312e; }
    .masthead { display: flex; align-items: center; justify-content: space-between; gap: 24px; }
    .brand { display: flex; align-items: center; gap: 13px; }
    .brand-mark { display: grid; width: 34px; height: 34px; place-items: center; border: 1px solid #7d7a73; border-radius: 50%; color: #d8ff5f; font-family: Georgia, serif; font-size: 19px; }
    .eyebrow { margin: 0 0 2px; color: #aaa7a0; font-size: 10px; letter-spacing: .15em; }
    h1 { font-family: Georgia, "Times New Roman", serif; font-size: clamp(25px, 3vw, 34px); font-weight: 500; letter-spacing: -.025em; }
    .subhead { margin: 5px 0 0 47px; color: #bbb8b1; font-size: 13px; }
    .header-actions { display: flex; align-items: center; gap: 10px; }
    .local-chip { padding: 7px 10px; border: 1px solid #4b4944; border-radius: 999px; color: #cbc8c0; font-size: 11px; font-weight: 750; }
    .theme-toggle { height: 34px; padding: 0 12px; border: 1px solid #f1eee7; border-radius: 999px; color: #171716; background: #fffdf9; font-size: 12px; }
    :root[data-theme="black"] .theme-toggle { border-color: #c8ff2f; color: #c8ff2f; background: #000; }
    main { padding: 24px 0 54px; }
    .status { margin: 0 0 12px; color: var(--muted); font-size: 12px; }
    .dot { width: 7px; height: 7px; background: var(--teal); }
    .collector-card, .filters, .panel, .stat { border: 1px solid var(--line); border-radius: 10px; background: var(--panel); box-shadow: none; }
    .collector-card { margin-bottom: 14px; padding: 14px 16px; }
    .collector-title small, .collector-fact small, .stat p { color: var(--muted); font-size: 10px; letter-spacing: .1em; }
    .collector-state { font-size: 15px; }
    .collector-fact b { color: var(--ink); font-size: 12px; }
    .collector-controls { grid-column:1 / -1; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px 16px; align-items:center; padding-top:12px; border-top:1px solid var(--line); }
    .collector-control-copy { min-width:0; }
    .collector-control-copy b { display:block; font-size:12px; }
    .collector-control-copy small { display:block; margin-top:3px; overflow:hidden; color:var(--muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
    .collector-actions { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:7px; }
    .collector-actions button { height:34px; padding:0 10px; font-size:11px; }
    .collector-actions button:disabled { cursor:not-allowed; opacity:.45; }
    .collector-feedback { grid-column:1 / -1; min-height:16px; margin:0; color:var(--muted); font-size:11px; }
    .collector-feedback.success { color:var(--teal); }
    .collector-feedback.error { color:#b42318; }
    .collector-tools { grid-column:1 / -1; border-top:1px solid var(--line); }
    .collector-tools summary { padding-top:10px; color:var(--muted); cursor:pointer; font-size:11px; font-weight:700; }
    .collector-tools-body { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:end; padding-top:10px; }
    .collector-log { max-height:170px; margin:0; overflow:auto; padding:11px; border:1px solid var(--line); border-radius:9px; color:var(--muted); background:var(--input); font:10px/1.55 Consolas,monospace; white-space:pre-wrap; }
    .danger-button { border:1px solid #d92d20; color:#b42318 !important; background:transparent !important; }
    .filter-stage { display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 24px; align-items: center; margin: 0 0 12px; padding: 18px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); }
    .filter-kicker { margin: 0 0 5px; color: var(--accent); font-size: 10px; font-weight: 850; letter-spacing: .12em; text-transform: uppercase; }
    .filter-intro h2 { margin: 0; font-family: Georgia, "Times New Roman", serif; font-size: 20px; font-weight: 500; }
    .filter-intro p:last-child { margin: 5px 0 0; color: var(--muted); font-size: 12px; line-height: 1.45; }
    .filters { grid-template-columns: minmax(180px, 1fr) repeat(4, 116px) auto; padding: 0; border: 0; border-radius: 0; background: transparent; }
    label { gap: 5px; color: var(--muted); font-size: 10px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }
    input { height: 38px; border: 1px solid var(--line); border-radius: 6px; color: var(--ink); background: var(--input); font-size: 13px; }
    input:focus { outline: 2px solid var(--accent-soft); border-color: var(--accent); }
    button { height: 38px; border-radius: 6px; background: var(--accent); color: #fff; font-size: 12px; letter-spacing: .01em; }
    :root[data-theme="black"] button { color: #10110c; }
    button.ghost { background: var(--accent-soft); color: var(--accent); }
    :root[data-theme="black"] button.ghost { color: var(--accent); }
    .quick { margin: 0 0 22px; gap: 6px; }
    .quick button { height: 28px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); background: var(--panel); font-size: 11px; }
    .quick button:hover { color: var(--accent); border-color: var(--accent); filter: none; }
    .stat-grid { gap: 10px; margin-bottom: 10px; }
    .stat { padding: 16px; border-radius: 9px; }
    .stat strong { margin-top: 5px; font-family: Georgia, "Times New Roman", serif; font-size: clamp(26px, 3vw, 34px); font-weight: 500; }
    .stat span { color: var(--muted); font-size: 11px; }
    .layout, .insight-grid { gap: 10px; margin-top: 10px; }
    .panel { border-radius: 9px; padding: 18px; }
    .panel h2 { font-family: Georgia, "Times New Roman", serif; font-size: 18px; font-weight: 500; }
    .panel .hint { color: var(--muted); font-size: 11px; }
    .bars { min-height: 178px; border-bottom-color: var(--line); }
    .bar { background: var(--accent); border-radius: 3px 3px 0 0; }
    .bar-label { color: var(--muted); }
    .track { background: color-mix(in srgb, var(--line) 75%, transparent); }
    .fill { background: var(--accent); }
    .domain-row b, .query-row b { color: var(--ink); }
    .query-row span { color: var(--accent); }
    .query-link:hover b, .visit-link:hover .visit-title { color: var(--accent); }
    .hour-grid { gap: 6px; }
    .hour-card { border-color: var(--line); border-radius: 6px; background: var(--input); }
    .hour-count, .hour-label { color: var(--muted); }
    .hour-meter { border-bottom-color: var(--line); }
    .hour-value { background: var(--accent); border-radius: 3px 3px 0 0; }
    .adult-stat { border-color: var(--line); border-radius: 6px; background: color-mix(in srgb, var(--accent-soft) 55%, var(--panel)); }
    .adult-stat span { color: var(--muted); }
    .adult-stat b { color: var(--accent); }
    .table-wrap { border-color: var(--line); border-radius: 7px; }
    th { color: var(--muted); background: color-mix(in srgb, var(--panel) 76%, var(--canvas)); font-size: 10px; }
    td { border-top-color: var(--line); }
    td.time, td.domain, .visit-url, .table-head p, .pager span, .privacy { color: var(--muted); }
    .privacy { font-size: 11px; }
    @media (max-width: 1060px) {
      .filter-stage { grid-template-columns: 1fr; gap: 12px; }
      .filters { grid-template-columns: minmax(180px, 1fr) repeat(4, 110px) auto; }
    }
    @media (max-width: 900px) {
      .masthead { align-items: flex-start; }
      .filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .filters label:first-child { grid-column: 1 / -1; }
    }
    @media (max-width: 540px) {
      .shell { width: min(100% - 24px, 1400px); }
      .masthead { flex-direction: column; }
      .subhead { margin-left: 0; }
      .header-actions { width: 100%; justify-content: space-between; }
      .filters { grid-template-columns: 1fr; }
      .filters label:first-child { grid-column: auto; }
    }

    :root {
      --ink: #1f1f1f;
      --muted: #5f6368;
      --line: #dfe3e8;
      --canvas: #f8faff;
      --panel: rgba(255, 255, 255, .92);
      --accent: #0b57d0;
      --accent-soft: #e8f0fe;
      --teal: #0f9d58;
      --header-bg: rgba(255, 255, 255, .86);
      --header-text: #202124;
      --input: rgba(255, 255, 255, .96);
      --soft-blue: #f1f6ff;
    }
    :root[data-theme="black"] {
      --ink: #f1f3f4;
      --muted: #b7bbc0;
      --line: #2b2f33;
      --canvas: #000000;
      --panel: #0a0b0c;
      --accent: #a8c7fa;
      --accent-soft: #18202b;
      --teal: #80e0ad;
      --header-bg: #000000;
      --header-text: #f1f3f4;
      --input: #111315;
      --soft-blue: #0d121a;
    }
    html { background: var(--canvas); }
    body {
      min-height: 100vh;
      font-family: "Google Sans", "Google Sans Text", "Segoe UI Variable", "Segoe UI", Arial, sans-serif;
      background:
        radial-gradient(900px 420px at 7% -8%, #e8f1ff 0%, transparent 68%),
        radial-gradient(760px 360px at 100% 0%, #edf7ff 0%, transparent 70%),
        var(--canvas);
    }
    :root[data-theme="black"] body { background: #000; }
    .shell { width: min(1360px, calc(100% - 48px)); }
    header {
      position: sticky;
      top: 0;
      z-index: 8;
      padding: 15px 0;
      color: var(--header-text);
      background: var(--header-bg);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(18px);
    }
    .masthead { min-height: 46px; }
    .brand { gap: 11px; }
    .brand-mark {
      width: 36px;
      height: 36px;
      border: 0;
      border-radius: 12px;
      color: #fff;
      background: var(--accent);
      font-family: "Google Sans", "Segoe UI", sans-serif;
      font-size: 18px;
      font-weight: 700;
    }
    :root[data-theme="black"] .brand-mark { color: #101418; }
    .eyebrow { color: var(--muted); font-family: "Google Sans", sans-serif; font-size: 10px; font-weight: 700; letter-spacing: .1em; }
    h1 { color: var(--header-text); font-family: "Google Sans", "Segoe UI", sans-serif; font-size: 22px; font-weight: 600; letter-spacing: -.025em; }
    .subhead { margin: 0 0 0 47px; color: var(--muted); font-size: 12px; }
    .local-chip { border-color: var(--line); color: var(--muted); background: var(--panel); font-size: 11px; }
    .theme-toggle { border-color: var(--line); color: var(--header-text); background: var(--panel); font-weight: 600; }
    :root[data-theme="black"] .theme-toggle { border-color: var(--line); color: var(--header-text); background: #0c0d0f; }
    main { padding: 24px 0 60px; }
    .status { font-size: 12px; }
    .collector-card, .filter-stage, .panel, .stat {
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 1px 2px rgba(60, 64, 67, .06), 0 1px 3px rgba(60, 64, 67, .04);
    }
    :root[data-theme="black"] .collector-card,
    :root[data-theme="black"] .filter-stage,
    :root[data-theme="black"] .panel,
    :root[data-theme="black"] .stat { box-shadow: none; }
    .collector-card { border-radius: 16px; }
    .filter-stage {
      grid-template-columns: 280px minmax(0, 1fr);
      gap: 28px;
      padding: 20px;
      border-radius: 16px;
      background: color-mix(in srgb, var(--panel) 88%, var(--soft-blue));
    }
    .filter-kicker { color: var(--accent); }
    .filter-intro h2 { color: var(--ink); font-family: "Google Sans", "Segoe UI", sans-serif; font-size: 19px; font-weight: 600; letter-spacing: -.015em; }
    .filters {
      grid-template-columns: minmax(220px, 1fr) minmax(168px, .52fr) minmax(148px, .42fr) auto;
      gap: 10px;
      align-items: end;
    }
    .period-tabs { display:flex; flex-wrap:wrap; gap:5px; margin-top:14px; }
    .period-tabs button { height:30px; padding:0 11px; border:1px solid var(--line); border-radius:999px; color:var(--muted); background:var(--panel); font-size:11px; }
    .period-tabs button.active { border-color:var(--accent); color:var(--accent); background:var(--accent-soft); font-weight:750; }
    .period-picker { min-width:168px; }
    .custom-period { display:grid; grid-template-columns:1fr 1fr; gap:8px; min-width:280px; }
    .global-filter-chip { align-self:flex-start; padding:5px 8px; border:1px solid var(--line); border-radius:99px; color:var(--muted); background:var(--input); font-size:10px; font-weight:700; white-space:nowrap; }
    .sidebar-utilities { display:grid; gap:8px; margin-top:12px; padding-top:12px; border-top:1px solid var(--line); }
    .sidebar-utilities button { width:100%; }
    @media (min-width: 1101px) {
      main.shell { width:min(1540px, calc(100% - 32px)); box-sizing:border-box; padding-left:306px; }
      .filter-stage { position:fixed; z-index:7; top:88px; left:max(16px, calc((100vw - 1540px) / 2 + 16px)); display:block; width:274px; max-height:calc(100vh - 104px); box-sizing:border-box; margin:0; overflow-y:auto; overscroll-behavior:contain; }
      .filter-stage .filter-intro { margin-bottom:16px; }
      .filter-stage .filters { display:grid; grid-template-columns:1fr; gap:11px; }
      .filter-stage .custom-period { grid-template-columns:1fr; min-width:0; }
      .filter-stage .minute-note { grid-column:auto; margin:0; }
      .filter-stage .actions { display:grid; grid-template-columns:1fr 1fr; }
      .filter-stage .time-popover { position:relative; top:auto; right:auto; width:auto; margin-top:8px; box-shadow:none; }
      .filter-stage .clock-face { transform:scale(.86); margin:-10px auto; }
    }
    label { color: var(--muted); font-size: 10px; font-weight: 650; }
    .picker-field, .time-field { position: relative; display: grid; gap: 5px; min-width: 0; }
    .field-label { color: var(--muted); font-size: 10px; font-weight: 650; letter-spacing: .06em; text-transform: uppercase; }
    .picker-trigger {
      display: flex;
      width: 100%;
      height: 38px;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 0 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      color: var(--ink);
      background: var(--input);
      text-align: left;
      font-family: inherit;
      font-size: 13px;
      font-weight: 500;
    }
    .picker-trigger:hover { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
    .picker-trigger:focus-visible { outline: 3px solid color-mix(in srgb, var(--accent) 16%, transparent); border-color: var(--accent); }
    .picker-trigger:disabled { cursor: not-allowed; color: var(--muted); background: color-mix(in srgb, var(--canvas) 72%, var(--panel)); }
    .picker-chevron { color: var(--muted); font-size: 15px; line-height: 1; }
    .picker-native-input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
    .time-popover {
      position: absolute;
      z-index: 20;
      top: calc(100% + 8px);
      right: 0;
      width: 292px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--panel);
      box-shadow: 0 14px 36px rgba(60, 64, 67, .20);
    }
    :root[data-theme="black"] .time-popover { box-shadow: 0 16px 34px rgba(0, 0, 0, .58); }
    .time-popover[hidden] { display: none; }
    .clock-display { display: flex; align-items: baseline; justify-content: center; gap: 3px; margin: 1px 0 12px; color: var(--ink); font-family: "Google Sans", "Segoe UI", sans-serif; font-size: 29px; font-weight: 600; letter-spacing: -.04em; }
    .clock-period { margin-left: 5px; color: var(--muted); font-size: 12px; font-weight: 700; letter-spacing: .06em; }
    .period-switch { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; margin-bottom: 12px; padding: 4px; border-radius: 10px; background: color-mix(in srgb, var(--soft-blue) 66%, var(--panel)); }
    .period-switch button { height: 31px; border: 0; border-radius: 7px; color: var(--muted); background: transparent; font-size: 12px; }
    .period-switch button.active { color: #fff; background: var(--accent); }
    .clock-instruction { margin: 0 0 9px; color: var(--muted); text-align: center; font-size: 11px; }
    .clock-face { position: relative; width: 230px; height: 230px; margin: 0 auto 12px; border: 1px solid var(--line); border-radius: 50%; background: color-mix(in srgb, var(--soft-blue) 61%, var(--panel)); touch-action: none; user-select: none; }
    .clock-hand { position: absolute; z-index: 1; top: 31px; left: calc(50% - 1px); width: 2px; height: 84px; border-radius: 3px; background: var(--accent); transform: rotate(var(--hand-angle)); transform-origin: 50% 100%; pointer-events: none; }
    .clock-center { position: absolute; z-index: 3; top: calc(50% - 8px); left: calc(50% - 8px); width: 16px; height: 16px; border-radius: 50%; background: var(--accent); pointer-events: none; }
    .clock-number { position: absolute; z-index: 2; top: 50%; left: 50%; width: 31px; height: 31px; padding: 0; border: 0; border-radius: 50%; color: var(--ink); background: transparent; transform: translate(-50%, -50%) rotate(var(--angle)) translateY(-86px) rotate(var(--counter-angle)); font-family: "Google Sans", "Segoe UI", sans-serif; font-size: 12px; font-weight: 600; }
    .clock-number:hover, .clock-number.selected { color: #fff; background: var(--accent); }
    .minute-entry { display: flex; align-items: center; justify-content: center; gap: 8px; margin: 0 0 12px; color: var(--muted); font-size: 11px; letter-spacing: normal; text-transform: none; }
    .minute-entry input { width: 60px; height: 32px; padding: 0 8px; text-align: center; font-variant-numeric: tabular-nums; }
    .time-popover-actions { display: flex; justify-content: space-between; gap: 8px; }
    .time-popover-actions button { height: 34px; padding: 0 12px; }
    input { border-color: var(--line); border-radius: 10px; background: var(--input); color: var(--ink); }
    input:disabled { cursor: not-allowed; color: var(--muted); background: color-mix(in srgb, var(--canvas) 72%, var(--panel)); }
    input:focus { outline: 3px solid color-mix(in srgb, var(--accent) 16%, transparent); border-color: var(--accent); }
    button { border-radius: 10px; font-family: "Google Sans", "Segoe UI", sans-serif; font-weight: 600; }
    .minute-note { grid-column: 2 / -1; margin: -3px 0 0; color: var(--muted); font-size: 11px; line-height: 1.35; }
    .quick { gap: 7px; }
    .quick button { border-radius: 10px; }
    .stat-grid { gap: 12px; }
    .stat { border-radius: 16px; padding: 18px; }
    .stat strong { color: var(--ink); font-family: "Google Sans", "Segoe UI", sans-serif; font-size: 30px; font-weight: 600; letter-spacing: -.035em; }
    .layout, .insight-grid { gap: 12px; }
    .panel { border-radius: 16px; padding: 20px; }
    .panel h2 { color: var(--ink); font-family: "Google Sans", "Segoe UI", sans-serif; font-size: 17px; font-weight: 600; letter-spacing: -.015em; }
    .hour-card { border-color: var(--line); border-radius: 10px; background: color-mix(in srgb, var(--panel) 76%, var(--soft-blue)); }
    .hour-panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
    .hour-panel-head .hint { margin-bottom: 0; }
    .hour-selection { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: -2px 0 12px; padding: 9px 10px; border: 1px solid color-mix(in srgb, var(--accent) 26%, var(--line)); border-radius: 10px; color: var(--ink); background: color-mix(in srgb, var(--accent-soft) 56%, var(--panel)); font-size: 12px; }
    .hour-selection button { height: 30px; padding: 0 9px; font-size: 11px; }
    button.hour-card { width: 100%; height: auto; padding: 7px 4px 5px; color: inherit; text-align: inherit; font-family: inherit; font-weight: inherit; cursor: pointer; }
    button.hour-card:hover { border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); background: color-mix(in srgb, var(--accent-soft) 58%, var(--panel)); }
    button.hour-card.active { border-color: var(--accent); background: color-mix(in srgb, var(--accent-soft) 76%, var(--panel)); box-shadow: inset 0 0 0 1px var(--accent); }
    .report-grid, .confidence-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .report-grid div, .confidence-grid div { min-width: 0; padding: 11px; border: 1px solid var(--line); border-radius: 10px; background: var(--input); }
    .report-grid small, .confidence-grid small { display: block; overflow: hidden; color: var(--muted); font-size: 10px; font-weight: 700; letter-spacing: .06em; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
    .report-grid b, .confidence-grid b { display: block; overflow: hidden; margin-top: 5px; color: var(--ink); font-family: "Google Sans", "Segoe UI", sans-serif; font-size: 15px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
    .calendar-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
    .calendar-cell { min-height: 50px; padding: 7px 5px; border: 1px solid var(--line); border-radius: 8px; background: var(--input); }
    .calendar-cell b, .calendar-cell span { display: block; font-variant-numeric: tabular-nums; }
    .calendar-cell b { color: var(--ink); font-size: 11px; }
    .calendar-cell span { margin-top: 7px; color: var(--muted); font-size: 10px; }
    .calendar-cell[data-level="1"] { background: color-mix(in srgb, var(--accent-soft) 35%, var(--panel)); }
    .calendar-cell[data-level="2"] { background: color-mix(in srgb, var(--accent-soft) 62%, var(--panel)); }
    .calendar-cell[data-level="3"] { border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); background: color-mix(in srgb, var(--accent-soft) 88%, var(--panel)); }
    .session-row, .journey-row, .revisit-row { padding: 10px 0; border-bottom: 1px solid var(--line); }
    .session-row:last-child, .journey-row:last-child, .revisit-row:last-child { border-bottom: 0; }
    .session-row b, .journey-row b, .revisit-row b { display: block; overflow: hidden; color: var(--ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
    .session-row small, .journey-row small, .revisit-row small { display: block; margin-top: 3px; overflow: hidden; color: var(--muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
    #sessions { max-height: 520px; overflow:auto; padding-right:6px; }
    .session-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:center; }
    .session-row .session-active { color:var(--accent); font-size:13px; font-weight:750; white-space:nowrap; }
    button.session-row { width:100%; height:auto; border:0; border-radius:9px; color:inherit; background:transparent; text-align:left; font-family:inherit; cursor:pointer; }
    button.session-row:hover { padding-right:9px; padding-left:9px; background:var(--input); }
    button.session-row:focus-visible { outline:3px solid color-mix(in srgb, var(--accent) 24%, transparent); }
    .session-row .session-active::after { content:' ›'; }
    .session-dialog { width:min(820px,calc(100vw - 28px)); max-height:min(820px,calc(100vh - 28px)); padding:0; overflow:hidden; border:1px solid var(--line); border-radius:18px; color:var(--ink); background:var(--panel); box-shadow:0 24px 80px rgba(0,0,0,.28); }
    .session-dialog::backdrop { background:rgba(12,18,28,.54); backdrop-filter:blur(3px); }
    .session-dialog-head { display:flex; justify-content:space-between; gap:18px; padding:20px 22px 16px; border-bottom:1px solid var(--line); }
    .session-dialog-head h2 { margin:0 0 4px; font-size:20px; }
    .session-dialog-head .hint { margin:0; }
    .session-dialog-close { width:36px; min-width:36px; height:36px; padding:0; border:1px solid var(--line); border-radius:50%; color:var(--ink); background:var(--input); font-size:20px; }
    .session-dialog-summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; padding:14px 22px; border-bottom:1px solid var(--line); }
    .session-dialog-summary div { padding:10px; border-radius:9px; background:var(--input); }
    .session-dialog-summary small,.session-dialog-summary b { display:block; }
    .session-dialog-summary small { color:var(--muted); font-size:9px; font-weight:750; letter-spacing:.06em; text-transform:uppercase; }
    .session-dialog-summary b { margin-top:4px; overflow:hidden; font-size:13px; text-overflow:ellipsis; white-space:nowrap; }
    .session-activity-list { max-height:calc(100vh - 245px); overflow:auto; padding:0 22px 18px; }
    .session-activity-item { display:grid; grid-template-columns:72px minmax(0,1fr) auto; gap:12px; align-items:start; padding:14px 0; border-bottom:1px solid var(--line); }
    .session-activity-item:last-child { border-bottom:0; }
    .session-activity-time { color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }
    .session-activity-page { min-width:0; }
    .session-activity-page a { display:block; overflow:hidden; color:var(--ink); font-size:13px; font-weight:700; line-height:1.35; text-decoration:none; text-overflow:ellipsis; white-space:nowrap; }
    .session-activity-page a:hover { color:var(--accent); }
    .session-activity-page small { display:block; overflow:hidden; margin-top:4px; color:var(--muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
    .session-activity-duration { color:var(--muted); font-size:10px; white-space:nowrap; }
    .browsing-time-panel { margin-top:16px; }
    .estimate-badge { padding:5px 8px; border:1px solid var(--line); border-radius:99px; color:var(--muted); background:var(--input); font-size:10px; font-weight:750; text-transform:uppercase; letter-spacing:.06em; }
    .time-summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:2px 0 18px; }
    .time-summary div { min-width:0; padding:12px; border:1px solid var(--line); border-radius:10px; background:var(--input); }
    .time-summary small,.time-summary b { display:block; } .time-summary small { color:var(--muted); font-size:10px; font-weight:750; text-transform:uppercase; letter-spacing:.06em; } .time-summary b { margin-top:5px; font-size:20px; }
    .daily-time-list { display:grid; gap:8px; }
    .daily-time-row { display:grid; grid-template-columns:92px minmax(0,1fr) 82px; gap:10px; align-items:center; font-size:12px; }
    .daily-time-row time { color:var(--muted); } .daily-time-row b { text-align:right; font-size:12px; }
    .daily-time-track { height:8px; overflow:hidden; border-radius:99px; background:var(--input); } .daily-time-fill { height:100%; border-radius:99px; background:var(--accent); }
    .journey-results { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 7px; }
    .journey-results a { max-width: 100%; overflow: hidden; padding: 4px 6px; border: 1px solid var(--line); border-radius: 7px; color: var(--accent); font-size: 10px; text-decoration: none; text-overflow: ellipsis; white-space: nowrap; }
    .revisit-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: center; }
    .revisit-row a { min-width: 0; color: inherit; text-decoration: none; }
    .revisit-row a:hover b { color: var(--accent); }
    .revisit-count { color: var(--accent); font-family: "Google Sans", "Segoe UI", sans-serif; font-size: 14px; font-weight: 700; }
    .visit-page { display: flex; align-items: center; gap: 9px; min-width: 0; }
    .visit-page .visit-link { flex: 1; min-width: 0; }
    button.visit-preview { width: 29px; height: 29px; min-width: 29px; padding: 0; border: 1px solid var(--line); border-radius: 8px; color: var(--muted); background: var(--input); font-size: 15px; line-height: 1; }
    button.visit-preview:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
    .visit-feed { border-top:1px solid var(--line); }
    .visit-card { display:grid; grid-template-columns:34px minmax(0,1fr) auto; gap:13px; align-items:start; padding:15px 2px; border-bottom:1px solid var(--line); }
    .visit-favicon { display:grid; place-items:center; width:28px; height:28px; margin-top:1px; border-radius:50%; color:#fff; background:var(--accent); font-family:"Google Sans","Segoe UI",sans-serif; font-size:12px; font-weight:700; }
    .visit-card-body { min-width:0; }
    .visit-source { display:block; margin-bottom:7px; color:var(--muted); font-size:12px; font-weight:650; }
    .visit-title { max-width:none; font-size:15px; line-height:1.4; white-space:normal; }
    .visit-url { max-width:none; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .visit-meta { display:flex; gap:8px; align-items:center; margin-top:9px; color:var(--muted); font-size:11px; }
    .visit-meta time { font-variant-numeric:tabular-nums; }
    .visit-media { position:relative; display:block; grid-column:3; grid-row:1; width:128px; height:72px; margin-top:34px; overflow:hidden; border-radius:9px; background:var(--input); }
    .visit-media.failed { display:none; }
    .visit-media:not(.ready)::before { content:'Checking…'; position:absolute; inset:0; display:grid; place-items:center; color:var(--muted); font-size:10px; }
    .visit-media img { width:100%; height:100%; object-fit:cover; opacity:0; }
    .visit-media.ready img { opacity:1; }
    .visit-media.ready::after { content:'Preview'; position:absolute; right:5px; bottom:4px; padding:2px 4px; border-radius:4px; color:#fff; background:rgba(0,0,0,.62); font-size:9px; }
    .visit-select { width:18px; height:18px; margin:8px 0; cursor:pointer; }
    .visit-card-actions { display:flex; gap:6px; align-items:center; }
    .visit-card-actions .visit-preview { margin-top:0; }
    .hour-value, .bar, .fill { background: var(--accent); }
    .table-wrap { border-radius: 12px; }
    th { background: color-mix(in srgb, var(--panel) 80%, var(--soft-blue)); }
    tr:hover td { background: color-mix(in srgb, var(--accent-soft) 32%, transparent); }
    .adult-stat { border-color: var(--line); background: color-mix(in srgb, var(--accent-soft) 44%, var(--panel)); }
    @media (max-width: 1100px) {
      .filter-stage { grid-template-columns: 1fr; gap: 16px; }
      .filters { grid-template-columns: minmax(220px, 1fr) minmax(168px, .52fr) minmax(148px, .42fr) auto; }
    }
    @media (max-width: 760px) {
      .filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .filters label:first-child, .minute-note, .actions { grid-column: 1 / -1; }
      .custom-period { grid-column:1 / -1; min-width:0; }
      .time-popover { right: auto; left: 0; }
      .visit-card { grid-template-columns:30px minmax(0,1fr); gap:9px; }
      .visit-card-actions { grid-column:2; grid-row:3; }
      .visit-media { grid-column:2; grid-row:4; width:min(176px,100%); height:99px; margin-top:0; }
      .time-summary { grid-template-columns:1fr; }
      .daily-time-row { grid-template-columns:76px minmax(0,1fr) 70px; }
      .hour-panel-head { display: grid; }
      .session-dialog-summary { grid-template-columns:1fr; }
      .session-activity-item { grid-template-columns:62px minmax(0,1fr); }
      .session-activity-duration { grid-column:2; }
      .collector-controls,.collector-tools-body { grid-template-columns:1fr; }
      .collector-actions { justify-content:flex-start; }
    }
    @media (max-width: 540px) {
      .shell { width: min(100% - 24px, 1360px); }
      header { position: static; }
      .filter-stage { padding: 16px; border-radius: 14px; }
      .period-tabs { display:grid; grid-template-columns:repeat(3,1fr); }
      .period-tabs button { width:100%; padding:0 6px; }
      .custom-period { grid-template-columns:1fr; }
      .collector-card, .panel, .stat { border-radius: 14px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="shell masthead">
      <div>
        <div class="brand">
          <span class="brand-mark">H</span>
          <div>
            <p class="eyebrow">Private local archive</p>
            <h1>Archive Review</h1>
          </div>
        </div>
        <p class="subhead">A clear view of what your Chrome history remembers.</p>
      </div>
      <div class="header-actions">
        <span class="local-chip">Only on this PC</span>
        <button type="button" class="theme-toggle" id="theme-toggle" aria-pressed="false">Black mode</button>
      </div>
    </div>
  </header>
  <main class="shell">
    <p class="status"><span class="dot"></span><span id="status">Loading private archive...</span></p>
    <section class="collector-card" aria-label="Collector status">
      <div class="collector-title">
        <small>Permanent collector</small>
        <div class="collector-state"><span class="collector-dot" id="collector-dot"></span><span id="collector-state">Checking...</span></div>
      </div>
      <div class="collector-fact"><small>Last update</small><b id="collector-last-update">Checking...</b></div>
      <div class="collector-fact"><small>Archive</small><b id="collector-archive">Checking...</b></div>
      <div class="collector-fact"><small>Automatic start</small><b id="collector-startup">Checking...</b></div>
      <button type="button" class="ghost" id="health-refresh">Check status</button>
      <div class="collector-controls">
        <div class="collector-control-copy"><b id="collector-process">Finding collector process…</b><small id="collector-next-scan">Checking the scan schedule…</small></div>
        <div class="collector-actions" aria-label="Collector controls">
          <button type="button" class="ghost" data-collector-action="check">Check now</button>
          <button type="button" class="ghost" data-collector-action="start">Start</button>
          <button type="button" class="ghost" id="collector-pause" data-collector-action="pause">Pause</button>
          <button type="button" class="ghost" data-collector-action="restart">Restart</button>
          <button type="button" class="ghost" data-collector-action="open_folder">Open archive folder</button>
        </div>
        <p class="collector-feedback" id="collector-feedback" aria-live="polite"></p>
        <details class="collector-tools">
          <summary>Recent collector log and advanced controls</summary>
          <div class="collector-tools-body">
            <pre class="collector-log" id="collector-log">Loading recent collector events…</pre>
            <button type="button" class="danger-button" id="collector-stop" data-collector-action="stop">Stop collector</button>
          </div>
        </details>
      </div>
    </section>
    <details class="panel" id="shared-sync" style="margin-bottom:20px">
      <summary><b>Shared desktop + Android archive</b></summary>
      <p id="sync-status" aria-live="polite">Checking shared archive…</p>
      <p class="hint">The PC is the permanent archive. Supabase temporarily holds mobile browsing records until this PC commits and verifies them locally, then deletes those cloud copies. Desktop history stays on this PC. Mobile records wait in the relay while the PC is offline. Use the same project and Google account on both devices.</p>
      <div style="display:flex;flex-wrap:wrap;gap:12px;margin:16px 0">
        <label style="flex:1;min-width:230px">Project URL<input id="sync-project" type="url" placeholder="https://PROJECT.supabase.co" autocomplete="off"></label>
        <label style="flex:1;min-width:230px">Public publishable / anon key<input id="sync-key" type="password" placeholder="Public key only" autocomplete="off"></label>
      </div>
      <input id="sync-full-history" type="checkbox" hidden disabled><p class="hint">Desktop uploads are disabled. Cloud deletion does not guarantee removal from provider logs, backups or storage remnants.</p>
      <p class="hint">First apply the shared-archive SQL setup and add http://127.0.0.1:8765/auth/callback to Supabase redirect URLs. On Android, use dev.datasync.archive.preview://auth/callback for the preview app.</p>
      <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:16px">
        <button type="button" class="ghost cloud-action" id="sync-save">Save connection</button>
        <button type="button" class="cloud-action" id="sync-login">Sign in with Google</button>
        <button type="button" class="ghost cloud-action" id="sync-now">Sync now</button>
        <button type="button" class="ghost cloud-action" id="sync-signout">Sign out</button>
      </div>
      <p class="hint">Profile 13 may already contain Chrome-synced mobile visits. Source labels identify how records were captured. Use Mobile to inspect original DataSync records separately; Combined can group likely duplicate observations without deleting them.</p>
    </details>
    <section class="filter-stage">
      <div class="filter-intro">
        <p class="filter-kicker">Universal view</p>
        <h2>Choose one period</h2>
        <p>Every dashboard, chart, search, and archived visit follows this range.</p>
        <div class="period-tabs" role="group" aria-label="Universal time period">
          <button type="button" data-period-mode="all" class="active">All time</button>
          <button type="button" data-period-mode="month">Month</button>
          <button type="button" data-period-mode="week">Week</button>
          <button type="button" data-period-mode="day">Day</button>
          <button type="button" data-period-mode="custom">Custom</button>
        </div>
      </div>
      <div class="period-tabs" role="group" aria-label="Archive view" style="margin:0 0 16px">
        <button type="button" data-archive-view="all" class="active" aria-pressed="true">Combined</button>
        <button type="button" data-archive-view="desktop" aria-pressed="false">Desktop archive</button>
        <button type="button" data-archive-view="android" aria-pressed="false">Mobile</button>
      </div>
      <p class="hint" id="archive-view-note" aria-live="polite"></p>
      <form class="filters" id="filters">
        <label>Search title, website, or link
          <input id="search" type="search" placeholder="youtube, news, banking">
        </label>
        <label>Archive view<select id="device-source"><option value="all">Combined</option><option value="desktop">Desktop archive</option><option value="android">Mobile / DataSync</option></select></label>
        <label>Duplicate observations<select id="duplicate-view"><option value="group">Group unique likely matches</option><option value="all">Show all original observations</option></select></label>
        <p class="hint" id="duplicate-summary">Matching uses full URLs where available, or legacy engine/query pairs, within 2 seconds. Original records are always retained.</p>
        <div class="picker-field period-picker" id="month-period-field" hidden>
          <span class="field-label">Month</span>
          <button class="picker-trigger" id="month-picker-button" type="button"><span id="month-picker-value">Choose a month</span><span class="picker-chevron">⌄</span></button>
          <input class="picker-native-input" id="period-month" type="month" aria-label="Choose dashboard month" tabindex="-1">
        </div>
        <div class="picker-field period-picker" id="week-period-field" hidden>
          <span class="field-label">Week containing</span>
          <button class="picker-trigger" id="week-picker-button" type="button"><span id="week-picker-value">Choose a week</span><span class="picker-chevron">⌄</span></button>
          <input class="picker-native-input" id="period-week" type="date" aria-label="Choose a date inside the dashboard week" tabindex="-1">
        </div>
        <div class="picker-field period-picker" id="day-period-field" hidden>
          <span class="field-label">On this day</span>
          <button class="picker-trigger" id="date-picker-button" type="button" aria-haspopup="dialog">
            <span id="date-picker-value">Choose a date</span><span class="picker-chevron" aria-hidden="true">⌄</span>
          </button>
          <input class="picker-native-input" id="moment-date" type="date" aria-label="Choose archive day" aria-describedby="minute-note" tabindex="-1">
        </div>
        <div class="custom-period" id="custom-period-field" hidden>
          <div class="picker-field"><span class="field-label">From</span><button class="picker-trigger" id="custom-start-button" type="button"><span id="custom-start-value">Start date</span><span class="picker-chevron">⌄</span></button><input class="picker-native-input" id="custom-start" type="date" aria-label="Custom range start date" tabindex="-1"></div>
          <div class="picker-field"><span class="field-label">To</span><button class="picker-trigger" id="custom-end-button" type="button"><span id="custom-end-value">End date</span><span class="picker-chevron">⌄</span></button><input class="picker-native-input" id="custom-end" type="date" aria-label="Custom range end date" tabindex="-1"></div>
        </div>
        <div class="time-field" id="time-field" hidden>
          <span class="field-label">At this minute</span>
          <button class="picker-trigger" id="time-picker-button" type="button" disabled aria-expanded="false" aria-controls="time-popover">
            <span id="time-picker-value">Choose a time</span><span class="picker-chevron" aria-hidden="true">⌄</span>
          </button>
          <div class="time-popover" id="time-popover" hidden role="dialog" aria-label="Choose an exact time">
            <div class="clock-display"><span id="clock-hour">12</span><span>:</span><span id="clock-minute">00</span><span class="clock-period" id="clock-period">AM</span></div>
            <div class="period-switch" aria-label="AM or PM">
              <button type="button" data-period="AM" class="active">AM</button>
              <button type="button" data-period="PM">PM</button>
            </div>
            <p class="clock-instruction" id="clock-instruction">Choose the hour</p>
            <div class="clock-face" id="clock-face" aria-label="Round time clock"></div>
            <label class="minute-entry">Exact minute
              <input id="minute-entry" type="number" min="0" max="59" inputmode="numeric" placeholder="00" aria-label="Exact minute from 0 to 59">
            </label>
            <div class="time-popover-actions">
              <button type="button" class="ghost" id="time-clear">Clear time</button>
              <button type="button" id="time-done">Done</button>
            </div>
          </div>
        </div>
        <div class="actions">
          <button type="submit">Apply</button>
          <button type="button" class="ghost" id="clear">Reset</button>
        </div>
        <p class="minute-note" id="minute-note"><b id="universal-range-label">All archived time</b> · Choose Day to optionally narrow the whole dashboard to one exact minute.</p>
      </form>
      <div class="sidebar-utilities"><button type="button" id="sidebar-refresh">Refresh synced data</button><button type="button" class="ghost" id="noise-toggle" aria-pressed="false">Hide noise</button></div>
    </section>

    <section class="stat-grid">
      <article class="stat"><p>Visits</p><strong id="visit-count">—</strong><span id="range-label">Selected period</span></article>
      <article class="stat"><p>Unique websites</p><strong id="domain-count">—</strong><span>Different domains visited</span></article>
      <article class="stat"><p>First visit</p><strong id="first-visit">—</strong><span>Within selected period</span></article>
      <article class="stat"><p>Latest visit</p><strong id="last-visit">—</strong><span>Within selected period</span></article>
      <article class="stat"><p>Searches</p><strong id="search-count">—</strong><span>Detected search-query visits</span></article>
      <article class="stat"><p>Unique queries</p><strong id="query-count">—</strong><span>Across search websites</span></article>
    </section>

    <section class="layout">
      <article class="panel">
        <h2>Visit activity</h2>
        <p class="hint">Daily visits in Sri Lanka time</p>
        <div class="bars" id="bars"></div>
      </article>
      <article class="panel">
        <h2>Most visited websites</h2>
        <p class="hint">Top domains in the selected period</p>
        <div class="domain-list" id="domains"></div>
      </article>
    </section>

    <section class="insight-grid">
      <article class="panel">
        <h2>Most searched queries</h2>
        <p class="hint">Click a query to open that exact saved search</p>
        <div class="query-list" id="queries"></div>
      </article>
      <article class="panel">
        <h2>Where you search</h2>
        <p class="hint">Websites that received search queries</p>
        <div class="domain-list" id="search-sources"></div>
      </article>
    </section>

    <section class="insight-grid">
      <article class="panel">
        <h2>Adult / NSFW activity insights</h2>
        <p class="hint">Potential matches from explicit searches, direct websites, page titles, and URL paths. Results stay only on this PC.</p>
        <div class="adult-summary">
          <div class="adult-stat"><span>Potential matches</span><b id="adult-search-count">—</b></div>
          <div class="adult-stat"><span>Direct-site visits</span><b id="adult-query-count">—</b></div>
        </div>
        <div class="query-list" id="adult-queries"></div>
      </article>
      <article class="panel">
        <h2>Adult / NSFW matching websites</h2>
        <p class="hint">Direct-site matches appear first. Click an activity item to open its saved page.</p>
        <div class="domain-list" id="adult-sources"></div>
      </article>
    </section>

    <section class="panel" id="nsfw-intelligence">
      <div class="panel-head"><div><p class="eyebrow">Private analysis</p><h2>NSFW review</h2><p class="hint">Potential matches only. Every result has a reason; no result is treated as certain.</p></div><button class="button secondary" id="nsfw-export" type="button">Export report</button></div>
      <div class="nsfw-stat-grid">
        <div class="nsfw-stat"><span>Potential matches</span><b id="nsfw-events">—</b></div>
        <div class="nsfw-stat"><span>Searches</span><b id="nsfw-searches">—</b></div>
        <div class="nsfw-stat"><span>Direct visits</span><b id="nsfw-direct">—</b></div>
        <div class="nsfw-stat"><span>High confidence</span><b id="nsfw-high">—</b></div>
      </div>
      <section class="nsfw-search-explorer" aria-labelledby="nsfw-explorer-title">
        <div class="nsfw-explorer-head"><div><h3 id="nsfw-explorer-title">NSFW search explorer</h3><p class="hint" id="nsfw-explorer-caption">Every detected search query, cluster, and keyword in the universal period.</p></div><span class="global-filter-chip">Uses universal period</span></div>
        <div class="nsfw-explorer-toolbar">
          <div class="nsfw-explorer-tabs" role="tablist" aria-label="NSFW search analysis"><button type="button" class="active" data-nsfw-view="queries">Exact queries</button><button type="button" data-nsfw-view="clusters">Clusters</button><button type="button" data-nsfw-view="keywords">Keywords</button></div>
          <div class="nsfw-source-filters" role="group" aria-label="NSFW search source"><button type="button" class="active" data-nsfw-source="all">All sources</button><button type="button" data-nsfw-source="google">Google</button><button type="button" data-nsfw-source="site">Inside websites</button></div>
          <input type="search" id="nsfw-explorer-filter" placeholder="Filter this list" aria-label="Filter NSFW search explorer">
        </div>
        <div class="nsfw-explorer-results" id="nsfw-explorer-results"><div class="empty">Loading detected searches…</div></div>
      </section>
      <details class="nsfw-details nsfw-more"><summary>More NSFW insights</summary><div class="nsfw-more-content"><div class="nsfw-browsing-time">
        <div class="panel-head"><div><h3>NSFW browsing time</h3><p class="hint">Estimated active time for potential NSFW matches in the universal period</p></div><span class="estimate-badge">Estimate</span></div>
        <div class="time-summary">
          <div><small>Selected period</small><b id="nsfw-browsing-total">—</b></div>
          <div><small>Average active day</small><b id="nsfw-browsing-average">—</b></div>
          <div><small>Active days</small><b id="nsfw-browsing-days">—</b></div>
        </div>
        <div class="daily-time-list" id="nsfw-daily-browsing-time"></div>
      </div>
      <div class="nsfw-primary-grid"><div class="nsfw-primary-card"><h3>Recent activity</h3><p class="hint" id="nsfw-comparison">Last 7 days compared with the previous 7 days.</p><div class="nsfw-timeline" id="nsfw-timeline"></div></div><div class="nsfw-primary-card"><h3>When it happens</h3><p class="hint">Sri Lanka time · hover a square for its count</p><div class="nsfw-heatmap" id="nsfw-heatmap"></div></div></div>
      <div class="nsfw-grid"><div><h3>Matching websites</h3><div class="domain-list" id="nsfw-sites"></div></div><div><h3>Query clusters</h3><p class="hint">Similar explicit search phrases grouped together</p><div class="query-list" id="nsfw-clusters"></div></div></div><div class="nsfw-grid"><div><h3>Confidence and reasons</h3><div class="domain-list" id="nsfw-confidence"></div><div class="domain-list compact-reasons" id="nsfw-reasons"></div></div><div><h3>Sessions and new websites</h3><div class="domain-list" id="nsfw-new-sites"></div><div id="nsfw-sessions"></div></div></div></div></details>
      <details class="nsfw-details"><summary>Adjust local classification rules</summary><p class="hint">One website or phrase per line. Rules never modify Chrome or the archive.</p><div class="nsfw-rule-grid"><label>Include websites<textarea id="rule-include-domains" placeholder="example.com"></textarea></label><label>Include terms<textarea id="rule-include-terms" placeholder="phrase"></textarea></label><label>Ignore websites<textarea id="rule-ignore-domains" placeholder="example.com"></textarea></label><label>Ignore terms<textarea id="rule-ignore-terms" placeholder="phrase"></textarea></label></div><div class="nsfw-rule-actions"><button class="button secondary" id="save-nsfw-rules" type="button">Save rules on this PC</button><p class="hint" id="nsfw-rules-status"></p></div></details>
    </section>

    <section class="insight-grid">
      <article class="panel chart-panel">
        <div class="hour-panel-head">
          <div>
            <h2>Time of day</h2>
            <p class="hint" id="hours-hint">Average visits by hour in Sri Lanka time. Click an hour to explore it.</p>
          </div>
          <span class="global-filter-chip">Uses universal period</span>
        </div>
        <div class="hour-selection" id="hour-selection" hidden>
          <span id="hour-selection-text"></span><button type="button" class="ghost" id="clear-hour">Clear hour</button>
        </div>
        <div class="hour-grid" id="hours"></div>
      </article>
      <article class="panel">
        <h2>Day of week</h2>
        <p class="hint">Your browsing pattern by weekday</p>
        <div class="compact-list" id="weekdays"></div>
      </article>
    </section>

    <section class="insight-grid">
      <article class="panel">
        <h2>Weekly local report</h2>
        <p class="hint" id="weekly-report-label">Latest 7 days in this view</p>
        <div class="report-grid">
          <div><small>Visits</small><b id="report-visits">—</b></div>
          <div><small>Top website</small><b id="report-domain">—</b></div>
          <div><small>Top category</small><b id="report-category">—</b></div>
          <div><small>Busiest time</small><b id="report-busy">—</b></div>
        </div>
      </article>
      <article class="panel">
        <h2>Archive confidence</h2>
        <p class="hint">Health of your permanent local collector</p>
        <div class="confidence-grid">
          <div><small>Confidence</small><b id="confidence-level">—</b></div>
          <div><small>Archive size</small><b id="confidence-size">—</b></div>
          <div><small>Recent archive activity</small><b id="confidence-recent">—</b></div>
        </div>
      </article>
    </section>

    <section class="layout">
      <article class="panel">
        <h2>Activity calendar</h2>
        <p class="hint">Recent active days in the selected period</p>
        <div class="calendar-grid" id="calendar"></div>
      </article>
      <article class="panel">
        <h2>Browsing categories</h2>
        <p class="hint">Local category rules based on website names and URLs</p>
        <div class="domain-list" id="categories"></div>
      </article>
    </section>

    <section class="panel browsing-time-panel">
      <div class="panel-head"><div><h2>Browsing time</h2><p class="hint">Estimated active time from Chrome visit duration and short gaps between visits</p></div><span class="estimate-badge">Estimate</span></div>
      <div class="time-summary">
        <div><small>Selected period</small><b id="browsing-total">—</b></div>
        <div><small>Average active day</small><b id="browsing-average">—</b></div>
        <div><small>Active days</small><b id="browsing-days">—</b></div>
      </div>
      <div class="daily-time-list" id="daily-browsing-time"></div>
    </section>

    <section class="insight-grid">
      <article class="panel">
        <h2>Detected browsing sessions</h2>
        <p class="hint" id="sessions-caption">A new session starts after 30 minutes without a recorded visit</p>
        <div class="compact-list" id="sessions"></div>
      </article>
      <article class="panel">
        <h2>Search to result journeys</h2>
        <p class="hint">Pages opened after a saved search within 30 minutes</p>
        <div class="journey-list" id="journeys"></div>
      </article>
    </section>

    <section class="panel">
      <h2>Most revisited pages</h2>
      <p class="hint">Pages you returned to repeatedly in the selected period</p>
      <div class="revisit-list" id="revisited"></div>
    </section>

    <section class="panel visits">
      <div class="table-head">
        <div><h2>Archived visits</h2><p id="table-caption">Newest first · open a page or load image previews on demand</p><p class="hint" id="preview-status" hidden></p></div>
        <div class="actions">
          <button type="button" class="ghost" id="select-visits">Select page</button>
          <button type="button" class="ghost" id="delete-visits" disabled>Delete selected (0)</button>
          <button type="button" class="ghost" id="load-previews" aria-pressed="false">Load image previews</button>
          <button type="button" class="ghost" id="export">Export CSV</button>
          <button type="button" class="ghost" id="refresh">Refresh</button>
        </div>
      </div>
      <div class="visit-feed" id="visit-rows"></div>
      <div class="pager">
        <button type="button" class="ghost" id="previous">Previous</button>
        <span id="page-label">Page 1</span>
        <button type="button" class="ghost" id="next">Next</button>
      </div>
    </section>
    <p class="privacy">This dashboard runs on this computer. Cloud sync is opt-in; when connected, selected archive data syncs to your private Supabase project. Sign out to stop uploads. Original local records are retained.</p>
  </main>
  <dialog class="session-dialog" id="session-dialog" aria-labelledby="session-dialog-title">
    <div class="session-dialog-head">
      <div><h2 id="session-dialog-title">Browsing session</h2><p class="hint" id="session-dialog-caption">Loading archived activity…</p></div>
      <button type="button" class="session-dialog-close" id="session-dialog-close" aria-label="Close session activity">×</button>
    </div>
    <div class="session-dialog-summary">
      <div><small>Session</small><b id="session-dialog-window">—</b></div>
      <div><small>Archived visits</small><b id="session-dialog-visits">—</b></div>
      <div><small>Most visited website</small><b id="session-dialog-domain">—</b></div>
    </div>
    <div class="session-activity-list" id="session-activity-list"><div class="empty">Loading session activity…</div></div>
  </dialog>
  <script>
    const state = { page: 1, limit: 500, rangeStart: "", rangeEnd: "", periodMode: "all", hour: null, hourScope: "custom", hideNoise: false, previews: false, previewChecked: 0, previewFound: 0, nsfwView: "queries", nsfwSource: "all", nsfwFilter: "", nsfwExplorer: null };
    let collectorCountdownTimer = null;
    const clockPicker = { hour: 12, minute: 0, period: "AM", mode: "hour", selected: false };
    const el = (id) => document.getElementById(id);
    const FILTER_STORAGE_KEY = "history-dashboard-universal-filter-v1";
    const persistUniversalPeriod = () => {
      try { localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify({ periodMode:state.periodMode, month:el("period-month").value, week:el("period-week").value, day:el("moment-date").value, customStart:el("custom-start").value, customEnd:el("custom-end").value, clock:{hour:clockPicker.hour, minute:clockPicker.minute, period:clockPicker.period, selected:clockPicker.selected} })); } catch (error) {}
    };
    const applyTheme = (theme) => {
      const black = theme === "black";
      document.documentElement.dataset.theme = black ? "black" : "white";
      el("theme-toggle").textContent = black ? "White mode" : "Black mode";
      el("theme-toggle").setAttribute("aria-pressed", String(black));
      try { localStorage.setItem("history-dashboard-theme", black ? "black" : "white"); } catch (error) {}
    };
    const escapeHtml = (value) => String(value || "").replace(/[&<>"']/g, (letter) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[letter]));
    const localDate = (value, concise) => {
      if (!value) return "—";
      const options = concise
        ? { timeZone: "Asia/Colombo", month: "short", day: "numeric" }
        : { timeZone: "Asia/Colombo", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: true };
      return new Intl.DateTimeFormat("en-LK", options).format(new Date(value));
    };
    const todayIso = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Colombo", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    const minusDays = (days) => {
      const point = new Date();
      point.setDate(point.getDate() - days);
      return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Colombo", year: "numeric", month: "2-digit", day: "2-digit" }).format(point);
    };
    const isoDate = (point) => [point.getFullYear(), String(point.getMonth() + 1).padStart(2, "0"), String(point.getDate()).padStart(2, "0")].join("-");
    const rangeForPeriod = (mode, selected) => {
      if (mode === "all") return {start:"", end:"", selected:""};
      if (mode === "month") {
        const month = selected || todayIso().slice(0, 7);
        const [year, monthNumber] = month.split("-").map(Number);
        const last = new Date(year, monthNumber, 0);
        return { start: month + "-01", end: isoDate(last), selected: month };
      }
      const point = new Date((selected || todayIso()) + "T12:00:00");
      if (mode === "week") {
        const mondayOffset = (point.getDay() + 6) % 7;
        point.setDate(point.getDate() - mondayOffset);
        const start = isoDate(point);
        point.setDate(point.getDate() + 6);
        return { start, end: isoDate(point), selected: selected || todayIso() };
      }
      const day = selected || todayIso();
      return { start: day, end: day, selected: day };
    };
    const readableDay = (day) => day ? new Intl.DateTimeFormat("en-LK", {day:"numeric", month:"short", year:"numeric", timeZone:"Asia/Colombo"}).format(new Date(day + "T12:00:00")) : "Choose a date";
    const syncUniversalPeriod = () => {
      document.querySelectorAll("[data-period-mode]").forEach((button) => button.classList.toggle("active", button.dataset.periodMode === state.periodMode));
      el("month-period-field").hidden = state.periodMode !== "month";
      el("week-period-field").hidden = state.periodMode !== "week";
      el("day-period-field").hidden = state.periodMode !== "day";
      el("custom-period-field").hidden = state.periodMode !== "custom";
      el("time-field").hidden = state.periodMode !== "day";
      if (state.periodMode === "month" && el("period-month").value) el("month-picker-value").textContent = new Intl.DateTimeFormat("en-LK", {month:"long", year:"numeric", timeZone:"Asia/Colombo"}).format(new Date(el("period-month").value + "-01T12:00:00"));
      if (state.periodMode === "week" && el("period-week").value) el("week-picker-value").textContent = "Week of " + readableDay(state.rangeStart);
      el("custom-start-value").textContent = el("custom-start").value ? readableDay(el("custom-start").value) : "Start date";
      el("custom-end-value").textContent = el("custom-end").value ? readableDay(el("custom-end").value) : "End date";
      el("universal-range-label").textContent = state.periodMode === "all" ? "All archived time" : (state.rangeStart === state.rangeEnd ? readableDay(state.rangeStart) : readableDay(state.rangeStart) + " – " + readableDay(state.rangeEnd));
    };
    const setUniversalPeriod = (mode, selected = "", refresh = true) => {
      state.periodMode = mode;
      let range;
      if (mode === "custom") {
        if (!el("custom-start").value) el("custom-start").value = minusDays(6);
        if (!el("custom-end").value) el("custom-end").value = todayIso();
        let start = el("custom-start").value;
        let end = el("custom-end").value;
        if (end < start) [start, end] = [end, start];
        range = {start, end};
      } else {
        range = rangeForPeriod(mode, selected);
        if (mode === "month") el("period-month").value = range.selected;
        if (mode === "week") el("period-week").value = range.selected;
        if (mode === "day") el("moment-date").value = range.selected;
      }
      state.rangeStart = range.start;
      state.rangeEnd = range.end;
      state.hourScope = ["month", "week", "day"].includes(mode) ? mode : "custom";
      state.hour = null;
      syncDatePicker();
      if (mode !== "day") clearTimePicker();
      updateMinuteAvailability();
      syncUniversalPeriod();
      state.page = 1;
      persistUniversalPeriod();
      if (refresh) load();
    };
    const restoreUniversalPeriod = () => {
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY) || "null"); } catch (error) {}
      const validDay = (value) => typeof value === "string" && /^\\d{4}-\\d{2}-\\d{2}$/.test(value);
      const validMonth = (value) => typeof value === "string" && /^\\d{4}-\\d{2}$/.test(value);
      if (!saved || !["all", "month", "week", "day", "custom"].includes(saved.periodMode)) { setUniversalPeriod("all", "", false); return; }
      if (validMonth(saved.month)) el("period-month").value = saved.month;
      if (validDay(saved.week)) el("period-week").value = saved.week;
      if (validDay(saved.day)) el("moment-date").value = saved.day;
      if (validDay(saved.customStart)) el("custom-start").value = saved.customStart;
      if (validDay(saved.customEnd)) el("custom-end").value = saved.customEnd;
      if (saved.periodMode === "day" && saved.clock && saved.clock.selected && Number.isInteger(saved.clock.hour) && saved.clock.hour >= 1 && saved.clock.hour <= 12 && Number.isInteger(saved.clock.minute) && saved.clock.minute >= 0 && saved.clock.minute <= 59 && ["AM", "PM"].includes(saved.clock.period)) {
        clockPicker.hour = saved.clock.hour; clockPicker.minute = saved.clock.minute; clockPicker.period = saved.clock.period; clockPicker.selected = true;
      }
      const selected = saved.periodMode === "month" ? el("period-month").value : saved.periodMode === "week" ? el("period-week").value : saved.periodMode === "day" ? el("moment-date").value : "";
      setUniversalPeriod(saved.periodMode, selected, false);
      syncTimePicker();
    };
    const displayPickerDate = (day) => {
      if (!day) return "Choose a date";
      return new Intl.DateTimeFormat("en-LK", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Colombo" }).format(new Date(day + "T12:00:00"));
    };
    const syncDatePicker = () => { el("date-picker-value").textContent = displayPickerDate(el("moment-date").value); };
    const displayClockTime = () => String(clockPicker.hour || 12).padStart(2, "0") + ":" + String(clockPicker.minute || 0).padStart(2, "0") + " " + clockPicker.period;
    const selectedClockTime = () => {
      if (!clockPicker.selected) return "";
      const hour24 = (clockPicker.hour % 12) + (clockPicker.period === "PM" ? 12 : 0);
      return String(hour24).padStart(2, "0") + ":" + String(clockPicker.minute).padStart(2, "0");
    };
    const renderClock = () => {
      const hours = clockPicker.mode === "hour";
      const choices = hours ? [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11] : [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];
      const selected = hours ? clockPicker.hour : clockPicker.minute;
      const handAngle = hours ? (clockPicker.hour % 12) * 30 : clockPicker.minute * 6;
      el("clock-face").innerHTML = '<span class="clock-hand" style="--hand-angle:' + handAngle + 'deg"></span><span class="clock-center"></span>' + choices.map((value, index) => {
        const angle = index * 30;
        const active = value === selected ? " selected" : "";
        const label = hours ? String(value) : String(value).padStart(2, "0");
        return '<button type="button" class="clock-number' + active + '" data-clock-value="' + value + '" style="--angle:' + angle + 'deg;--counter-angle:-' + angle + 'deg" aria-label="Choose ' + label + (hours ? ' hour' : ' minutes') + '">' + label + '</button>';
      }).join("");
    };
    const syncTimePicker = () => {
      el("clock-hour").textContent = String(clockPicker.hour || 12).padStart(2, "0");
      el("clock-minute").textContent = String(clockPicker.minute || 0).padStart(2, "0");
      el("clock-period").textContent = clockPicker.period;
      el("time-picker-value").textContent = clockPicker.selected ? displayClockTime() : "Choose a time";
      el("clock-instruction").textContent = clockPicker.mode === "hour" ? "Choose the hour" : "Choose the minute";
      document.querySelectorAll("[data-period]").forEach((button) => button.classList.toggle("active", button.dataset.period === clockPicker.period));
      el("minute-entry").value = String(clockPicker.minute || 0).padStart(2, "0");
      renderClock();
    };
    const closeTimePicker = () => {
      el("time-popover").hidden = true;
      el("time-picker-button").setAttribute("aria-expanded", "false");
    };
    const openTimePicker = () => {
      if (el("time-picker-button").disabled) return;
      clockPicker.mode = "hour";
      el("time-popover").hidden = false;
      el("time-picker-button").setAttribute("aria-expanded", "true");
      syncTimePicker();
    };
    const clearTimePicker = () => {
      clockPicker.hour = 12;
      clockPicker.minute = 0;
      clockPicker.period = "AM";
      clockPicker.mode = "hour";
      clockPicker.selected = false;
      syncTimePicker();
      closeTimePicker();
    };
    const nextMinute = (day, minute) => {
      const point = new Date(day + "T" + minute + ":00");
      point.setMinutes(point.getMinutes() + 1);
      return { day: isoDate(point), minute: String(point.getHours()).padStart(2, "0") + ":" + String(point.getMinutes()).padStart(2, "0") };
    };
    const updateMinuteAvailability = () => {
      const hasDay = state.periodMode === "day" && Boolean(el("moment-date").value);
      el("time-picker-button").disabled = !hasDay;
      if (!hasDay) clearTimePicker();
    };
    const parameters = () => {
      const values = new URLSearchParams();
      values.set('source', el('device-source').value);
      values.set('duplicates', el('duplicate-view').value);
      if (el("search").value.trim()) values.set("search", el("search").value.trim());
      const day = el("moment-date").value;
      const minute = selectedClockTime();
      if (state.periodMode === "day" && day) {
        values.set("start", day);
        values.set("end", day);
        if (minute) {
          const ending = nextMinute(day, minute);
          values.set("start_time", minute);
          values.set("end", ending.day);
          values.set("end_time", ending.minute);
        }
      } else if (state.periodMode !== "all") {
        if (state.rangeStart) values.set("start", state.rangeStart);
        if (state.rangeEnd) values.set("end", state.rangeEnd);
      }
      if (state.hour !== null) values.set("hour", String(state.hour));
      values.set("hour_scope", state.hourScope);
      if (state.hideNoise) values.set("clean", "1");
      values.set("page", String(state.page));
      values.set("limit", String(state.limit));
      return values;
    };
    const displayBars = (items) => {
      const target = el("bars");
      if (!items.length) {
        target.innerHTML = '<div class="empty">No visits in this period.</div>';
        return;
      }
      const maximum = Math.max(...items.map((item) => item.count), 1);
      target.innerHTML = items.map((item) => {
        const height = Math.max(3, Math.round((item.count / maximum) * 178));
        return '<div class="bar-wrap"><div class="bar" style="height:' + height + 'px" title="' + escapeHtml(item.label + ': ' + item.count + ' visits') + '"></div><span class="bar-label">' + escapeHtml(item.short_label) + '</span></div>';
      }).join("");
    };
    const displayDomains = (items, targetId, emptyText) => {
      const target = el(targetId);
      if (!items.length) {
        target.innerHTML = '<div class="empty">' + emptyText + '</div>';
        return;
      }
      const maximum = Math.max(...items.map((item) => item.count), 1);
      target.innerHTML = items.map((item) => {
        const width = Math.round((item.count / maximum) * 100);
        return '<div class="domain-row"><div><b title="' + escapeHtml(item.domain) + '">' + escapeHtml(item.domain) + '</b><div class="track"><div class="fill" style="width:' + width + '%"></div></div></div><span>' + item.count + '</span></div>';
      }).join("");
    };
    const displayQueries = (items, targetId, emptyText) => {
      const target = el(targetId);
      if (!items.length) {
        target.innerHTML = '<div class="empty">' + emptyText + '</div>';
        return;
      }
      target.innerHTML = items.map((item) => {
        if (targetId !== "queries") return '<div class="query-row"><a class="query-link" href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener"><div><b>' + escapeHtml(item.query) + '</b><small>' + escapeHtml(item.websites) + '</small></div><span>' + item.count + '</span></a></div>';
        return '<div class="query-row"><button type="button" class="ghost delete-query" data-query="' + escapeHtml(item.query) + '" style="float:right;margin:4px">Delete</button><a class="query-link" href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener" title="Open this saved search"><div><b title="' + escapeHtml(item.query) + '">' + escapeHtml(item.query) + '</b><small title="' + escapeHtml(item.websites) + '">' + escapeHtml(item.websites) + '</small></div><span>' + item.count + '</span></a></div>';
      }).join("");
    };
    document.addEventListener("click", async (event) => {
      const button = event.target.closest(".delete-query");
      if (!button) return;
      const query = button.dataset.query;
      if (!confirm('Delete all archived searches for “' + query + '” across desktop and mobile, regardless of filters? Chrome, cloud, backups and previously exported files may still contain copies.')) return;
      button.disabled = true;
      try {
        const response = await fetch('/api/delete-search', {method:'POST', headers:{'Content-Type':'application/json', 'X-DataSync':'1'}, body:JSON.stringify({query})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Deletion failed');
        alert(result.message);
        load();
      } catch (error) { alert(error.message); button.disabled = false; }
    });
    const displayHours = (items) => {
      const target = el("hours");
      const maximum = Math.max(...items.map((item) => item.value), 1);
      target.innerHTML = items.map((item) => {
        const height = Math.max(3, Math.round((item.value / maximum) * 78));
        const selected = item.hour === state.hour ? " active" : "";
        return '<button type="button" class="hour-card' + selected + '" data-archive-hour="' + item.hour + '" aria-pressed="' + String(item.hour === state.hour) + '" title="' + escapeHtml(item.label + ': ' + item.display + ' shown · ' + item.count + ' total visits') + '"><span class="hour-count">' + escapeHtml(item.display) + '</span><span class="hour-meter"><span class="hour-value" style="height:' + height + 'px"></span></span><span class="hour-label">' + escapeHtml(item.label) + '</span></button>';
      }).join("");
    };
    const displayWeekdays = (items) => {
      const target = el("weekdays");
      const maximum = Math.max(...items.map((item) => item.count), 1);
      target.innerHTML = items.map((item) => {
        const width = Math.round((item.count / maximum) * 100);
        return '<div class="domain-row"><div><b>' + escapeHtml(item.label) + '</b><div class="track"><div class="fill" style="width:' + width + '%"></div></div></div><span>' + item.count + '</span></div>';
      }).join("");
    };
    const applyCollectorStatus = (collector) => {
      const dot = el("collector-dot");
      el("collector-state").textContent = collector.label;
      dot.className = "collector-dot " + (collector.healthy ? "healthy" : "warning");
      if (collector.last_update) {
        el("collector-last-update").textContent = localDate(collector.last_update, false) + " · " + collector.seconds_since_update + "s ago";
      } else {
        el("collector-last-update").textContent = "No collector update found";
      }
      el("collector-archive").textContent = collector.archive_available ? "Available" : "Missing";
      el("collector-startup").textContent = collector.automatic_start_installed ? "Installed" : "Not installed";
      const processLabel = collector.process_ids.length ? "PID " + collector.process_ids.join(", ") : (collector.running ? "Running process detected" : "No collector process");
      el("collector-process").textContent = collector.paused ? "Paused · " + processLabel : processLabel;
      el("collector-log").textContent = collector.log_lines.length ? collector.log_lines.join("\\n") : "No collector log entries yet.";
      const pause = el("collector-pause");
      pause.textContent = collector.paused ? "Resume" : "Pause";
      pause.dataset.collectorAction = collector.paused ? "resume" : "pause";
      pause.disabled = !collector.running;
      el("collector-stop").disabled = !collector.running;
      document.querySelector('[data-collector-action="check"]').disabled = collector.paused;
      if (collectorCountdownTimer) clearInterval(collectorCountdownTimer);
      const schedule = el("collector-next-scan");
      if (collector.paused) {
        schedule.textContent = "Automatic scans are paused until you resume.";
      } else if (!collector.running) {
        schedule.textContent = "Start the collector to resume automatic scans.";
      } else {
        let remaining = collector.next_scan_seconds === null ? collector.check_interval_seconds : collector.next_scan_seconds;
        const updateCountdown = () => { schedule.textContent = remaining > 0 ? "Next automatic scan in about " + remaining + "s · every " + collector.check_interval_seconds + "s" : "Automatic scan is due now…"; remaining = Math.max(0, remaining - 1); };
        updateCountdown();
        collectorCountdownTimer = setInterval(updateCountdown, 1000);
      }
    };
    const runCollectorControl = async (action, button) => {
      if (action === "stop" && !window.confirm("Stop permanent collection? Visits can be missed until it is started again or Windows signs in next time.")) return;
      const feedback = el("collector-feedback");
      feedback.className = "collector-feedback";
      feedback.textContent = action === "open_folder" ? "Opening the private archive folder…" : "Applying collector control…";
      const controls = [...document.querySelectorAll("[data-collector-action]")];
      let resolvedCollector = null;
      controls.forEach((control) => control.disabled = true);
      try {
        const response = await fetch("/api/collector/control", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Collector control failed.");
        resolvedCollector = data.collector;
        applyCollectorStatus(data.collector);
        feedback.className = "collector-feedback success";
        feedback.textContent = data.message;
        if (["check","start","resume","restart"].includes(action)) setTimeout(load, 1600);
      } catch (error) {
        feedback.className = "collector-feedback error";
        feedback.textContent = error.message || "The collector control could not be completed.";
        setTimeout(load, 800);
      } finally {
        controls.forEach((control) => control.disabled = false);
        if (resolvedCollector) applyCollectorStatus(resolvedCollector);
      }
    };
    const visitLabel = (rawUrl, domain) => {
      try { const parsed = new URL(rawUrl); const host = parsed.hostname.replace(/^www\\./, ""); if (host.includes("youtube.com") || host === "youtu.be") return "YouTube · video"; if (host.includes("google.") && parsed.pathname.startsWith("/search")) return "Google · search"; if (host.includes("images.google")) return "Google · image search"; if (/\\.(jpg|jpeg|png|gif|webp|avif)(?:$|\\?)/i.test(parsed.pathname)) return host + " · image"; return host; } catch (error) { return domain || "Archived visit"; }
    };
    const faviconLetter = (domain) => (domain || "H").replace(/^www\\./, "").charAt(0).toUpperCase();
    const updatePreviewStatus = () => {
      if (!state.previews) return;
      el("preview-status").textContent = state.previewChecked
        ? state.previewFound + " preview" + (state.previewFound === 1 ? "" : "s") + " found · " + state.previewChecked + " visible visit" + (state.previewChecked === 1 ? "" : "s") + " checked"
        : "Looking for available previews in the visible visits…";
    };
    const showThumbnail = async (image) => {
      if (image.dataset.loaded) return;
      image.dataset.loaded = "true";
      const container = image.parentElement;
      try {
        const response = await fetch("/api/thumbnail?url=" + encodeURIComponent(image.dataset.thumbnailUrl), {cache:"no-store"});
        const data = await response.json();
        if (!data.image_url) { container.classList.add("failed"); return; }
        const markReady = () => { if (container.classList.contains("ready")) return; container.classList.add("ready"); state.previewFound += 1; updatePreviewStatus(); };
        image.addEventListener("load", markReady, {once:true});
        image.addEventListener("error", () => container.classList.add("failed"), {once:true});
        image.src = data.image_url;
        if (image.complete && image.naturalWidth) markReady();
      } catch (error) { container.classList.add("failed"); }
      finally { state.previewChecked += 1; updatePreviewStatus(); }
    };
    const loadVisibleThumbnails = () => {
      const images = [...document.querySelectorAll("img[data-thumbnail-url]")];
      if (!("IntersectionObserver" in window)) { images.forEach(showThumbnail); return; }
      const observer = new IntersectionObserver((entries) => { entries.forEach((entry) => { if (!entry.isIntersecting) return; observer.unobserve(entry.target); showThumbnail(entry.target); }); }, {rootMargin:"180px"});
      images.forEach((image) => observer.observe(image));
    };
    let visitItems = [];
    const selectedVisits = new Set();
    const visitKey = item => String(item.archive_id) + ":" + item.delete_refs.map(ref => ref.digest).join(":");
    const updateVisitSelection = () => {
      const boxes = [...document.querySelectorAll('.visit-select')];
      el('delete-visits').disabled = selectedVisits.size === 0;
      el('delete-visits').textContent = 'Delete selected (' + selectedVisits.size + ')';
      el('select-visits').textContent = boxes.length && boxes.every(box => box.checked) ? 'Clear page' : 'Select page';
    };
    const displayVisits = (items) => {
      visitItems = items;
      const visible = new Set(items.map(visitKey));
      for (const id of selectedVisits) if (!visible.has(id)) selectedVisits.delete(id);
      const target = el("visit-rows");
      if (!items.length) {
        target.innerHTML = el('device-source').value === 'android'
          ? '<div class="empty">No mobile records in this range. Mobile shows only DataSync captures received by this PC; phone visits received through Chrome sync remain in Desktop archive.</div>'
          : '<div class="empty">No matching archived visits.</div>';
        updateVisitSelection();
        return;
      }
      target.innerHTML = items.map((item) => {
        const title = item.title || "Untitled page";
        const mobile = (item.capture_source || '').startsWith('chrome_android');
        const manual = item.capture_source === 'manual_import';
        const quality = manual ? (item.url_quality === 'user_supplied_precise' ? ' · imported visit timestamp' : ' · time supplied to the minute') + (item.url_quality === 'incomplete' ? ' · incomplete URL' : '') : mobile && ['address_bar','inferred_scheme'].includes(item.url_quality) ? ' · address-bar fallback (may be incomplete)' : mobile && item.url_quality === 'document' ? ' · document URL' : '';
        const sourceLabel = (item.duplicate_status === 'grouped' ? 'Profile 13 + Android · likely duplicate grouped' : (manual ? 'Manually supplied record' : mobile ? 'Android browsing capture' : 'Profile 13 archive') + (item.duplicate_status === 'possible' ? ' · possible duplicate' : '')) + quality + (item.tab_mode === 'incognito' ? ' · Incognito' : '');
        const media = state.previews ? '<div class="visit-media"><img alt="" data-thumbnail-url="' + escapeHtml(item.url) + '"></div>' : '';
        return '<article class="visit-card"><div class="visit-favicon" aria-hidden="true">' + escapeHtml(faviconLetter(item.domain)) + '</div><div class="visit-card-body"><span class="visit-source">' + escapeHtml(sourceLabel + " · " + visitLabel(item.url, item.domain)) + '</span><a class="visit-link" href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener" title="Open this archived visit in a new tab"><span class="visit-title" title="' + escapeHtml(title) + '">' + escapeHtml(title) + '</span><span class="visit-url" title="' + escapeHtml(item.url) + '">' + escapeHtml(item.url) + '</span></a><div class="visit-meta"><time>' + escapeHtml(localDate(item.visited_at_utc, false)) + '</time><span>·</span><span>' + escapeHtml(item.domain || "Archived visit") + '</span></div></div><div class="visit-card-actions"><input class="visit-select" type="checkbox" data-visit-id="' + visitKey(item) + '" aria-label="Select visit" ' + (selectedVisits.has(visitKey(item)) ? 'checked' : '') + '><button type="button" class="visit-preview" data-preview-url="' + escapeHtml(item.url) + '" aria-label="Open a compact preview for ' + escapeHtml(title) + '" title="Open compact preview">◫</button></div>' + media + '</article>';
      }).join("");
      updateVisitSelection();
      if (state.previews) { state.previewChecked = 0; state.previewFound = 0; updatePreviewStatus(); loadVisibleThumbnails(); }
    };
    let syncSettingsLoaded = false;
    const refreshSync = async () => {
      try {
        const response = await fetch('/api/sync/status', {cache:'no-store'});
        const data = await response.json();
        if (!syncSettingsLoaded && typeof data.include_visits === 'boolean') {
          el('sync-full-history').checked = data.include_visits;
          syncSettingsLoaded = true;
        }
        el('sync-status').textContent = data.error || ((data.signed_in ? 'Relay connected as ' + data.email : data.configured ? 'Configured — sign in to connect' : 'Not connected — configure your relay') + ' · ' + (data.shared_searches || 0) + ' permanently saved searches · ' + (data.shared_visits || 0) + ' retained legacy web copies · ' + (data.relay_deleted || 0) + ' cloud copies consumed' + (data.last_sync ? ' · last relay check ' + localDate(data.last_sync, false) : ''));
        if (!el('sync-project').value && data.project) el('sync-project').value = data.project;
      } catch (error) { el('sync-status').textContent = 'Shared sync status unavailable; local archive remains available.'; }
    };
    const cloudAction = async (action, payload={}) => {
      document.querySelectorAll('.cloud-action').forEach(button => button.disabled = true);
      try {
        const response = await fetch('/api/sync/' + action, {method:'POST', headers:{'Content-Type':'application/json','X-DataSync':'1'}, body:JSON.stringify(payload)});
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || 'Sync action failed');
        if (data.url) { window.location.assign(data.url); return; }
        if (action === 'configure') el('sync-key').value = '';
        await refreshSync();
      } catch (error) { el('sync-status').textContent = error.message; }
      finally { document.querySelectorAll('.cloud-action').forEach(button => button.disabled = false); }
    };
    const displayCalendar = (items) => {
      const target = el("calendar");
      if (!items.length) { target.innerHTML = '<div class="empty">No activity in this period.</div>'; return; }
      const maximum = Math.max(...items.map((item) => item.count), 1);
      target.innerHTML = items.map((item) => {
        const level = item.count === 0 ? 0 : Math.min(3, Math.ceil((item.count / maximum) * 3));
        return '<div class="calendar-cell" data-level="' + level + '" title="' + escapeHtml(item.date + ': ' + item.count + ' visits') + '"><b>' + escapeHtml(item.date.slice(8)) + '</b><span>' + item.count + ' visit' + (item.count === 1 ? '' : 's') + '</span></div>';
      }).join("");
    };
    const formatDuration = (seconds) => { const minutes = Math.max(0, Math.round(seconds / 60)); if (minutes < 60) return minutes + ' min'; return Math.floor(minutes / 60) + 'h ' + String(minutes % 60).padStart(2, '0') + 'm'; };
    const localClock = (value) => value ? new Intl.DateTimeFormat("en-LK", {timeZone:"Asia/Colombo", hour:"numeric", minute:"2-digit", hour12:true}).format(new Date(value)) : "—";
    const displaySessions = (items) => {
      const target = el("sessions");
      if (!items.length) { target.innerHTML = '<div class="empty">No sessions in this period.</div>'; return; }
      target.innerHTML = items.map((item) => '<button type="button" class="session-row" data-session-start="' + escapeHtml(item.start) + '" data-session-last="' + escapeHtml(item.last_visit) + '" aria-label="Show all ' + item.visits + ' archived visits in this browsing session"><div><b>' + escapeHtml(localDate(item.start, false)) + ' · ' + item.visits + ' visits</b><small>' + escapeHtml(localClock(item.start) + '–' + localClock(item.end) + ' · ' + formatDuration(item.span_seconds) + ' span · ' + item.top_domain) + '</small></div><span class="session-active">' + escapeHtml(formatDuration(item.active_seconds)) + ' active</span></button>').join("");
    };
    const displaySessionActivity = (data) => {
      el("session-dialog-caption").textContent = "Every archived page recorded between the first and last visit in this detected session.";
      el("session-dialog-window").textContent = localClock(data.start) + "–" + localClock(data.end);
      el("session-dialog-visits").textContent = data.visits.toLocaleString();
      el("session-dialog-domain").textContent = data.top_domain || "—";
      const target = el("session-activity-list");
      if (!data.items.length) { target.innerHTML = '<div class="empty">No archived activity was found for this session.</div>'; return; }
      target.innerHTML = data.items.map((item) => {
        const seconds = Math.max(0, Math.round((item.visit_duration || 0) / 1000000));
        const duration = seconds ? formatDuration(seconds) + " recorded" : "";
        return '<article class="session-activity-item"><time class="session-activity-time">' + escapeHtml(localClock(item.visited_at_utc)) + '</time><div class="session-activity-page"><a href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener" title="Open this archived visit in a new tab">' + escapeHtml(item.title || "Untitled page") + '</a><small title="' + escapeHtml(item.url) + '">' + escapeHtml((item.domain || "Archived visit") + " · " + item.url) + '</small></div><span class="session-activity-duration">' + escapeHtml(duration) + '</span></article>';
      }).join("");
    };
    const openSessionActivity = async (button) => {
      const dialog = el("session-dialog");
      el("session-dialog-caption").textContent = "Loading archived activity…";
      el("session-dialog-window").textContent = localClock(button.dataset.sessionStart) + "–" + localClock(button.dataset.sessionLast);
      el("session-dialog-visits").textContent = "—";
      el("session-dialog-domain").textContent = "—";
      el("session-activity-list").innerHTML = '<div class="empty">Loading session activity…</div>';
      if (!dialog.open) dialog.showModal();
      try {
        const query = new URLSearchParams({start:button.dataset.sessionStart, end:button.dataset.sessionLast});
        query.set('source',el('device-source').value);
        query.set('duplicates',el('duplicate-view').value);
        const response = await fetch("/api/session?" + query.toString(), {cache:"no-store"});
        if (!response.ok) throw new Error("Session could not be read");
        displaySessionActivity(await response.json());
      } catch (error) {
        el("session-dialog-caption").textContent = "The session could not be loaded.";
        el("session-activity-list").innerHTML = '<div class="empty">Please close this window and try again.</div>';
      }
    };
    const displayBrowsingTime = (timeData, prefix = "") => {
      el(prefix + "browsing-total").textContent = formatDuration(timeData.total_seconds);
      el(prefix + "browsing-average").textContent = formatDuration(timeData.average_daily_seconds);
      el(prefix + "browsing-days").textContent = timeData.active_days.toLocaleString();
      const target = el(prefix + "daily-browsing-time");
      if (!timeData.daily.length) { target.innerHTML = '<div class="empty">No browsing time in this period.</div>'; return; }
      const maximum = Math.max(...timeData.daily.map((item) => item.seconds), 1);
      target.innerHTML = timeData.daily.map((item) => '<div class="daily-time-row"><time datetime="' + escapeHtml(item.date) + '">' + escapeHtml(new Intl.DateTimeFormat("en-LK", {timeZone:"UTC", month:"short", day:"numeric"}).format(new Date(item.date + "T00:00:00Z"))) + '</time><div class="daily-time-track" title="' + escapeHtml(formatDuration(item.seconds) + ' active · ' + item.visits + ' visits') + '"><div class="daily-time-fill" style="width:' + Math.round(item.seconds / maximum * 100) + '%"></div></div><b>' + escapeHtml(formatDuration(item.seconds)) + '</b></div>').join("");
    };
    const displayJourneys = (items) => {
      const target = el("journeys");
      if (!items.length) { target.innerHTML = '<div class="empty">No saved search journeys in this period.</div>'; return; }
      target.innerHTML = items.map((item) => '<div class="journey-row"><a class="query-link" href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener"><div><b>' + escapeHtml(item.query) + '</b><small>' + escapeHtml(item.source + ' · ' + localDate(item.visited_at, false)) + '</small></div></a><div class="journey-results">' + item.results.map((result) => '<a href="' + escapeHtml(result.url) + '" target="_blank" rel="noopener" title="' + escapeHtml(result.title) + '">' + escapeHtml(result.domain) + '</a>').join("") + '</div></div>').join("");
    };
    const displayRevisited = (items) => {
      const target = el("revisited");
      if (!items.length) { target.innerHTML = '<div class="empty">No repeated pages in this period.</div>'; return; }
      target.innerHTML = items.map((item) => '<div class="revisit-row"><a href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener"><b title="' + escapeHtml(item.title) + '">' + escapeHtml(item.title) + '</b><small>' + escapeHtml(item.domain) + '</small></a><span class="revisit-count">' + item.count + '×</span></div>').join("");
    };
    const displayNsfwTimeline = (items) => {
      const target = el("nsfw-timeline");
      if (!items.length) { target.innerHTML = '<div class="empty">No potential matches in this period.</div>'; return; }
      const maximum = Math.max(...items.map((item) => item.count), 1);
      target.innerHTML = items.map((item) => '<span style="height:' + Math.max(3, Math.round(item.count / maximum * 100)) + '%" title="' + escapeHtml(item.date + ': ' + item.count + ' potential match' + (item.count === 1 ? '' : 'es')) + '"></span>').join("");
    };
    const displayNsfwHeatmap = (items) => {
      const target = el("nsfw-heatmap"); const maximum = Math.max(...items.map((item) => item.count), 1); const days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
      let output = '<span></span>' + Array.from({length:24}, (_, hour) => '<b>' + String(hour).padStart(2,"0") + '</b>').join("");
      days.forEach((day, weekday) => { output += '<b>' + day + '</b>'; for (let hour=0; hour<24; hour += 1) { const item = items.find((value) => value.weekday === weekday && value.hour === hour) || {count:0}; const level = item.count ? Math.min(3, Math.ceil(item.count / maximum * 3)) : 0; output += '<span class="nsfw-cell" data-level="' + level + '" title="' + day + ' ' + String(hour).padStart(2,"0") + ':00 · ' + item.count + ' potential matches"></span>'; } }); target.innerHTML = output;
    };
    const displayNsfwSessions = (items) => {
      const target = el("nsfw-sessions");
      if (!items.length) { target.innerHTML = '<div class="empty">No potential-match sessions in this period.</div>'; return; }
      target.innerHTML = items.map((item) => '<div class="nsfw-session"><b>' + item.events + ' potential matches · ' + escapeHtml(formatDuration(item.active_seconds || 0)) + ' active</b><small>' + escapeHtml(item.minutes + ' min span · ' + localDate(item.start, false) + ' · ' + item.top_site) + '</small></div>').join("");
    };
    const displayNsfwExplorer = (explorer) => {
      state.nsfwExplorer = explorer;
      const target = el("nsfw-explorer-results");
      document.querySelectorAll("[data-nsfw-view]").forEach((button) => { button.classList.toggle("active", button.dataset.nsfwView === state.nsfwView); button.setAttribute("aria-selected", String(button.dataset.nsfwView === state.nsfwView)); });
      document.querySelectorAll("[data-nsfw-source]").forEach((button) => { button.classList.toggle("active", button.dataset.nsfwSource === state.nsfwSource); button.setAttribute("aria-pressed", String(button.dataset.nsfwSource === state.nsfwSource)); });
      const sourceMatches = (item) => state.nsfwSource === "all" || item.sources.some((source) => source.group === state.nsfwSource);
      const matchingSources = (item) => item.sources.filter((source) => state.nsfwSource === "all" || source.group === state.nsfwSource);
      const sourceCount = (item) => matchingSources(item).reduce((total, source) => total + source.count, 0);
      const sourceText = (item) => matchingSources(item).map((source) => source.group_label + " · " + source.domain + " (" + source.count + "×)").join(" · ");
      const needle = state.nsfwFilter.trim().toLocaleLowerCase();
      const searchable = (item) => {
        const related = (item.queries || []).map((query) => query.query).join(" ");
        const primary = item.query || item.cluster || item.keyword || "";
        return !needle || (primary + " " + related + " " + item.sources.map((source) => source.domain).join(" ")).toLocaleLowerCase().includes(needle);
      };
      const items = (explorer[state.nsfwView] || []).filter(sourceMatches).filter(searchable);
      el("nsfw-explorer-caption").textContent = explorer.unique_queries + " exact queries · " + explorer.unique_clusters + " clusters · " + explorer.unique_keywords + " keywords · " + explorer.sources + " search websites";
      if (!items.length) { target.innerHTML = '<div class="empty">No detected searches match these filters.</div>'; return; }
      target.innerHTML = items.map((item) => {
        if (state.nsfwView === "queries") { const sources = matchingSources(item); const link = state.nsfwSource === "all" ? item.url : sources[0].url; const latest = state.nsfwSource === "all" ? item.latest : sources.map((source) => source.latest).sort().at(-1); return '<article class="nsfw-explorer-row"><a href="' + escapeHtml(link) + '" target="_blank" rel="noopener" title="Open this exact saved search"><b>' + escapeHtml(item.query) + '</b><small>' + escapeHtml(sourceText(item) + " · last seen " + localDate(latest, false)) + '</small></a><span class="nsfw-explorer-count">' + sourceCount(item) + '×</span></article>'; }
        const title = state.nsfwView === "clusters" ? item.cluster : item.keyword;
        const related = item.queries.map((query) => query.query + (query.count > 1 ? " (" + query.count + "×)" : "")).join(" · ");
        return '<article class="nsfw-explorer-row"><div><b>' + escapeHtml(title) + '</b><small>' + escapeHtml(sourceText(item)) + '</small><div class="nsfw-related-phrases">' + escapeHtml(related) + '</div></div><span class="nsfw-explorer-count">' + sourceCount(item) + '×</span></article>';
      }).join("");
    };
    const textRuleValues = (key) => el("rule-" + key.replace(/_/g, "-")).value.split(/\\n|,/).map((value) => value.trim()).filter(Boolean);
    const loadNsfwRules = async () => {
      try { const response = await fetch("/api/nsfw-rules", {cache:"no-store"}); if (!response.ok) return; const rules = await response.json(); Object.entries(rules).forEach(([key, values]) => { const field = el("rule-" + key.replace(/_/g, "-")); if (field) field.value = values.join("\\n"); }); } catch (error) { /* Dashboard analytics remain available without rules. */ }
    };
    const saveNsfwRules = async () => {
      const payload = { include_domains:textRuleValues("include_domains"), include_terms:textRuleValues("include_terms"), ignore_domains:textRuleValues("ignore_domains"), ignore_terms:textRuleValues("ignore_terms") };
      el("nsfw-rules-status").textContent = "Saving local rules…";
      try { const response = await fetch("/api/nsfw-rules", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)}); if (!response.ok) throw new Error("save failed"); el("nsfw-rules-status").textContent = "Saved locally. Refreshing analysis…"; await load(); el("nsfw-rules-status").textContent = "Saved locally."; } catch (error) { el("nsfw-rules-status").textContent = "Rules could not be saved."; }
    };
    const validPreviewUrl = (value) => {
      try { const parsed = new URL(value); return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : ""; } catch (error) { return ""; }
    };
    const openPreview = (url) => {
      const safeUrl = validPreviewUrl(url);
      if (!safeUrl) return;
      const preview = window.open(safeUrl, "ChromeHistoryPreview", "popup=yes,width=1100,height=760,noopener,noreferrer");
      if (!preview) window.open(safeUrl, "_blank", "noopener,noreferrer");
    };
    const syncNoiseToggle = () => {
      el("noise-toggle").textContent = state.hideNoise ? "Noise hidden" : "Hide noise";
      el("noise-toggle").setAttribute("aria-pressed", String(state.hideNoise));
    };
    const applyData = (data) => {
      applyCollectorStatus(data.collector_status);
      el("visit-count").textContent = data.summary.visits.toLocaleString();
      el('duplicate-summary').textContent = el('device-source').value !== 'all' ? 'All original records from this capture source are shown. Possible duplicate labels are review hints; no records are merged or deleted in this view.' : (data.summary.grouped_observations || 0) + ' likely duplicate pairs grouped in this range; ' + (data.summary.flagged_observations || 0) + ' observations flagged. Full URLs or legacy engine/query within 2 seconds; ambiguous matches, explicit shares and known Incognito stay separate. Original records are retained. Choose all observations to review/export both sources.';
      el("domain-count").textContent = data.summary.domains.toLocaleString();
      el("first-visit").textContent = localDate(data.summary.first_visit, true);
      el("last-visit").textContent = localDate(data.summary.last_visit, true);
      el("search-count").textContent = data.searches.events.toLocaleString();
      el("query-count").textContent = data.searches.unique_queries.toLocaleString();
      el("adult-search-count").textContent = data.searches.adult.events.toLocaleString();
      el("adult-query-count").textContent = data.searches.adult.direct_events.toLocaleString();
      el("nsfw-events").textContent = data.nsfw.events.toLocaleString();
      el("nsfw-searches").textContent = data.nsfw.searches.toLocaleString();
      el("nsfw-direct").textContent = data.nsfw.direct.toLocaleString();
      el("nsfw-high").textContent = data.nsfw.high_confidence.toLocaleString();
      el("range-label").textContent = data.summary.range_label;
      el("weekly-report-label").textContent = data.insights.weekly_report.label;
      el("report-visits").textContent = data.insights.weekly_report.visits.toLocaleString();
      el("report-domain").textContent = data.insights.weekly_report.top_domain;
      el("report-category").textContent = data.insights.weekly_report.top_category;
      el("report-busy").textContent = data.insights.weekly_report.busiest_day + ' · ' + data.insights.weekly_report.busiest_hour;
      el("confidence-level").textContent = data.collector_status.confidence;
      el("confidence-size").textContent = data.collector_status.archive_size_mb.toFixed(2) + ' MB';
      el("confidence-recent").textContent = data.collector_status.seconds_since_update === null ? 'No recent update' : data.collector_status.seconds_since_update + 's ago';
      el("table-caption").textContent = data.visits.total.toLocaleString() + " matching visit" + (data.visits.total === 1 ? "" : "s") + " · newest first";
      el("page-label").textContent = "Page " + data.visits.page + " of " + Math.max(data.visits.pages, 1);
      el("previous").disabled = data.visits.page <= 1;
      el("next").disabled = data.visits.page >= data.visits.pages;
      displayBars(data.trend);
      displayDomains(data.top_domains, "domains", "No websites in this period.");
      displayQueries(data.searches.top_queries, "queries", "No recognised search queries in this period.");
      displayDomains(data.searches.sources, "search-sources", "No recognised search websites in this period.");
      displayQueries(data.searches.adult.top_queries, "adult-queries", "No potential Adult / NSFW activity was recognised in this period.");
      displayDomains(data.searches.adult.sources, "adult-sources", "No potential Adult / NSFW websites were recognised in this period.");
      displayNsfwTimeline(data.nsfw.timeline);
      displayNsfwHeatmap(data.nsfw.heatmap);
      displayNsfwExplorer(data.nsfw.search_explorer);
      displayDomains(data.nsfw.confidence.map((item) => ({domain:item.label, count:item.count})), "nsfw-confidence", "No confidence levels in this period.");
      displayDomains(data.nsfw.reasons.map((item) => ({domain:item.label, count:item.count})), "nsfw-reasons", "No match reasons in this period.");
      displayDomains(data.nsfw.sources, "nsfw-sites", "No matching websites in this period.");
      displayQueries(data.nsfw.clusters, "nsfw-clusters", "No matching search clusters in this period.");
      displayDomains(data.nsfw.new_sites, "nsfw-new-sites", "No new matching websites in this period.");
      displayNsfwSessions(data.nsfw.sessions);
      displayBrowsingTime(data.nsfw.browsing_time, "nsfw-");
      const comparison = data.nsfw.comparison; el("nsfw-comparison").textContent = comparison.label + " · " + comparison.recent + " matches versus " + comparison.prior + " in the previous 7 days" + (comparison.change === null ? "." : " (" + (comparison.change >= 0 ? "+" : "") + comparison.change + "%).");
      el("hours-hint").textContent = data.patterns.description;
      el("hour-selection").hidden = data.patterns.selected_hour === null;
      if (data.patterns.selected_hour !== null) {
        el("hour-selection-text").textContent = "Showing searches, websites, and archived visits from " + String(data.patterns.selected_hour).padStart(2, "0") + ":00 to " + String(data.patterns.selected_hour).padStart(2, "0") + ":59.";
      }
      displayHours(data.patterns.hours);
      displayWeekdays(data.patterns.weekdays);
      displayVisits(data.visits.items);
      displayCalendar(data.insights.calendar);
      displayDomains(data.insights.categories, "categories", "No category data in this period.");
      displayBrowsingTime(data.insights.browsing_time);
      el("sessions-caption").textContent = data.insights.sessions.length.toLocaleString() + " detected session" + (data.insights.sessions.length === 1 ? "" : "s") + " · active time is estimated; a new session starts after a 30-minute gap";
      displaySessions(data.insights.sessions);
      displayJourneys(data.insights.journeys);
      displayRevisited(data.insights.revisited);
      syncNoiseToggle();
      el("status").textContent = "Archive loaded locally · updated " + new Intl.DateTimeFormat("en-LK", { timeZone: "Asia/Colombo", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date());
    };
    let loadSequence = 0;
    async function load() {
      const sequence = ++loadSequence;
      el("status").textContent = "Loading private archive...";
      try {
        const response = await fetch("/api/dashboard?" + parameters().toString(), { cache: "no-store" });
        if (!response.ok) throw new Error("The archive could not be read.");
        const data = await response.json();
        if (sequence === loadSequence) applyData(data);
      } catch (error) {
        if (sequence !== loadSequence) return;
        el("status").textContent = "Could not load the archive. Keep the collector files in their current folder.";
      }
    }
    el("filters").addEventListener("submit", (event) => { event.preventDefault(); persistUniversalPeriod(); state.page = 1; load(); });
    const dateChanged = () => {
      if (el("moment-date").value) setUniversalPeriod("day", el("moment-date").value);
    };
    document.querySelectorAll("[data-period-mode]").forEach((button) => button.addEventListener("click", () => { if (button.dataset.periodMode !== state.periodMode) clearTimePicker(); setUniversalPeriod(button.dataset.periodMode); }));
    el("date-picker-button").addEventListener("click", () => {
      try { el("moment-date").showPicker(); } catch (error) { el("moment-date").focus(); }
    });
    el("moment-date").addEventListener("change", dateChanged);
    el("month-picker-button").addEventListener("click", () => { try { el("period-month").showPicker(); } catch (error) { el("period-month").focus(); } });
    el("period-month").addEventListener("change", () => { if (el("period-month").value) setUniversalPeriod("month", el("period-month").value); });
    el("week-picker-button").addEventListener("click", () => { try { el("period-week").showPicker(); } catch (error) { el("period-week").focus(); } });
    el("period-week").addEventListener("change", () => { if (el("period-week").value) setUniversalPeriod("week", el("period-week").value); });
    el("custom-start-button").addEventListener("click", () => { try { el("custom-start").showPicker(); } catch (error) { el("custom-start").focus(); } });
    el("custom-end-button").addEventListener("click", () => { try { el("custom-end").showPicker(); } catch (error) { el("custom-end").focus(); } });
    const customRangeChanged = () => { if (el("custom-start").value && el("custom-end").value) setUniversalPeriod("custom"); };
    el("custom-start").addEventListener("change", customRangeChanged);
    el("custom-end").addEventListener("change", customRangeChanged);
    el("hours").addEventListener("click", (event) => {
      const card = event.target.closest("[data-archive-hour]");
      if (!card) return;
      state.hour = Number(card.dataset.archiveHour);
      state.page = 1;
      load();
    });
    el("clear-hour").addEventListener("click", () => {
      state.hour = null;
      state.page = 1;
      load();
    });
    el("noise-toggle").addEventListener("click", () => {
      state.hideNoise = !state.hideNoise;
      state.page = 1;
      load();
    });
    el('visit-rows').addEventListener('change', (event) => {
      if (!event.target.matches('.visit-select')) return;
      const id = event.target.dataset.visitId;
      if (event.target.checked) selectedVisits.add(id); else selectedVisits.delete(id);
      updateVisitSelection();
    });
    el('select-visits').addEventListener('click', () => {
      const boxes = [...document.querySelectorAll('.visit-select')];
      const select = !boxes.every(box => box.checked);
      boxes.forEach(box => { box.checked = select; if (select) selectedVisits.add(box.dataset.visitId); else selectedVisits.delete(box.dataset.visitId); });
      updateVisitSelection();
    });
    el('delete-visits').addEventListener('click', async () => {
      const chosen = visitItems.filter(item => selectedVisits.has(visitKey(item)));
      if (!chosen.length || !confirm('Delete ' + chosen.length + ' selected visit(s) from the local archives? Grouped rows delete both observations. Chrome, other devices, cloud copies, backups and exports may still contain copies.')) return;
      el('delete-visits').disabled = true;
      try {
        const response = await fetch('/api/delete-visits', {method:'POST', headers:{'Content-Type':'application/json','X-DataSync':'1'}, body:JSON.stringify({visits:chosen.flatMap(item => item.delete_refs)})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Deletion failed');
        selectedVisits.clear(); alert(result.message); await load();
      } catch (error) { alert(error.message); updateVisitSelection(); }
    });
    el("visit-rows").addEventListener("click", (event) => {
      const preview = event.target.closest(".visit-preview");
      if (!preview) return;
      event.preventDefault();
      openPreview(preview.dataset.previewUrl);
    });
    el("sessions").addEventListener("click", (event) => {
      const session = event.target.closest("[data-session-start]");
      if (session) openSessionActivity(session);
    });
    el("session-dialog-close").addEventListener("click", () => el("session-dialog").close());
    el("session-dialog").addEventListener("click", (event) => { if (event.target === el("session-dialog")) el("session-dialog").close(); });
    el("time-picker-button").addEventListener("click", () => {
      if (el("time-popover").hidden) openTimePicker(); else closeTimePicker();
    });
    document.querySelectorAll("[data-period]").forEach((button) => button.addEventListener("click", () => {
      clockPicker.period = button.dataset.period;
      clockPicker.selected = true;
      syncTimePicker();
    }));
    el("clock-face").addEventListener("click", (event) => {
      const choice = event.target.closest("[data-clock-value]");
      if (!choice) return;
      const value = Number(choice.dataset.clockValue);
      if (clockPicker.mode === "hour") {
        clockPicker.hour = value;
        clockPicker.mode = "minute";
      } else {
        clockPicker.minute = value;
      }
      clockPicker.selected = true;
      syncTimePicker();
    });
    el("clock-face").addEventListener("pointerdown", (event) => {
      if (clockPicker.mode !== "minute") return;
      const rect = el("clock-face").getBoundingClientRect();
      const x = event.clientX - rect.left - rect.width / 2;
      const y = event.clientY - rect.top - rect.height / 2;
      if (Math.hypot(x, y) < 25) return;
      let degrees = Math.atan2(x, -y) * 180 / Math.PI;
      if (degrees < 0) degrees += 360;
      clockPicker.minute = Math.round(degrees / 6) % 60;
      clockPicker.selected = true;
      syncTimePicker();
    });
    const applyTypedMinute = () => {
      const raw = el("minute-entry").value.trim();
      if (raw === "") return;
      const value = Number(raw);
      if (!Number.isInteger(value) || value < 0 || value > 59) return;
      clockPicker.minute = value;
      clockPicker.selected = true;
      syncTimePicker();
    };
    el("minute-entry").addEventListener("input", applyTypedMinute);
    el("minute-entry").addEventListener("change", () => { applyTypedMinute(); syncTimePicker(); });
    el("time-clear").addEventListener("click", () => { clearTimePicker(); persistUniversalPeriod(); });
    el("time-done").addEventListener("click", () => { persistUniversalPeriod(); closeTimePicker(); });
    document.addEventListener("pointerdown", (event) => {
      if (!el("time-field").contains(event.target)) closeTimePicker();
    });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeTimePicker(); });
    el("clear").addEventListener("click", () => {
      el("search").value = "";
      clearTimePicker();
      setUniversalPeriod("all");
    });
    el("refresh").addEventListener("click", load);
    el("sidebar-refresh").addEventListener("click", load);
    el("health-refresh").addEventListener("click", load);
    document.querySelector(".nsfw-explorer-tabs").addEventListener("click", (event) => {
      const view = event.target.closest("[data-nsfw-view]");
      if (!view) return;
      state.nsfwView = view.dataset.nsfwView;
      displayNsfwExplorer(state.nsfwExplorer);
    });
    document.querySelector(".nsfw-source-filters").addEventListener("click", (event) => {
      const source = event.target.closest("[data-nsfw-source]");
      if (!source) return;
      state.nsfwSource = source.dataset.nsfwSource;
      displayNsfwExplorer(state.nsfwExplorer);
    });
    el("nsfw-explorer-filter").addEventListener("input", (event) => { state.nsfwFilter = event.target.value; displayNsfwExplorer(state.nsfwExplorer); });
    document.querySelector(".collector-controls").addEventListener("click", (event) => {
      const button = event.target.closest("[data-collector-action]");
      if (button && !button.disabled) runCollectorControl(button.dataset.collectorAction, button);
    });
    el("theme-toggle").addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "black" ? "white" : "black");
    });
    el("export").addEventListener("click", () => { window.location.href = "/api/export.csv?" + parameters().toString(); });
    el("load-previews").addEventListener("click", () => { state.previews = !state.previews; el("load-previews").textContent = state.previews ? "Hide image previews" : "Load image previews"; el("load-previews").setAttribute("aria-pressed", String(state.previews)); el("preview-status").hidden = !state.previews; el("preview-status").textContent = state.previews ? "Looking for available previews in the visible visits…" : ""; load(); });
    el("nsfw-export").addEventListener("click", () => { window.location.href = "/api/nsfw.csv?" + parameters().toString(); });
    el("save-nsfw-rules").addEventListener("click", saveNsfwRules);
    el("previous").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; load(); } });
    el("next").addEventListener("click", () => { state.page += 1; load(); });
    let combinedDuplicateView = el('duplicate-view').value;
    const selectArchiveView = (source, reload = true) => {
      if (!['all', 'desktop', 'android'].includes(source)) source = 'all';
      if (!el('duplicate-view').disabled) combinedDuplicateView = el('duplicate-view').value;
      el('device-source').value = source;
      el('duplicate-view').disabled = source !== 'all';
      el('duplicate-view').value = source === 'all' ? combinedDuplicateView : 'all';
      document.querySelectorAll('[data-archive-view]').forEach((button) => {
        const selected = button.dataset.archiveView === source;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', String(selected));
      });
      el('archive-view-note').textContent = source === 'android'
        ? 'Mobile shows original DataSync phone records only. All charts and exports follow this view. A possible match in Desktop archive does not hide the mobile record.'
        : source === 'desktop'
        ? 'Desktop archive shows Profile 13 records, including any phone visits delivered by Chrome sync. It identifies the collection path, not necessarily the device used to browse.'
        : 'Combined includes both capture paths. Likely duplicate grouping is optional; the separate views always show original records.';
      const address = new URL(window.location.href);
      if (source === 'all') address.searchParams.delete('view');
      else address.searchParams.set('view', source === 'android' ? 'mobile' : 'desktop');
      history.replaceState(null, '', address);
      state.page = 1;
      if (reload) load();
    };
    document.querySelectorAll('[data-archive-view]').forEach((button) => {
      button.addEventListener('click', () => selectArchiveView(button.dataset.archiveView));
    });
    el('device-source').addEventListener('change', () => selectArchiveView(el('device-source').value));
    el('duplicate-view').addEventListener('change', () => { state.page = 1; load(); });
    el('sync-save').addEventListener('click', () => cloudAction('configure', {url:el('sync-project').value, key:el('sync-key').value, include_visits:el('sync-full-history').checked}));
    el('sync-login').addEventListener('click', () => cloudAction('login'));
    el('sync-now').addEventListener('click', () => cloudAction('now'));
    el('sync-signout').addEventListener('click', () => cloudAction('signout'));
    try { applyTheme(localStorage.getItem("history-dashboard-theme") || "white"); } catch (error) { applyTheme("white"); }
    const initialView = new URLSearchParams(window.location.search).get('view');
    selectArchiveView(initialView === 'mobile' ? 'android' : initialView === 'desktop' ? 'desktop' : 'all', false);
    syncDatePicker();
    syncTimePicker();
    updateMinuteAvailability();
    loadNsfwRules();
    restoreUniversalPeriod();
    refreshSync();
    setInterval(() => { refreshSync(); if (!document.hidden) load(); }, 30000);
    load();
  </script>
</body>
</html>
"""


def shared_bridge() -> Bridge:
    global SHARED_BRIDGE
    if SHARED_BRIDGE is None:
        SHARED_BRIDGE = Bridge(ARCHIVE_DATABASE, ARCHIVE_DIRECTORY / 'SharedArchive.sqlite')
    return SHARED_BRIDGE


def _archive_connection(duplicates='group') -> sqlite3.Connection:
    if not ARCHIVE_DATABASE.is_file():
        raise FileNotFoundError("The permanent archive database was not found.")
    connection = sqlite3.connect(
        ARCHIVE_DATABASE.resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    connection.execute('CREATE TEMP TABLE manual_visit_provenance(archive_id INTEGER PRIMARY KEY, url_incomplete INTEGER, timestamp_precision TEXT)')
    if connection.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='manual_visit_provenance'").fetchone():
        connection.execute('INSERT INTO temp.manual_visit_provenance SELECT archive_id,url_incomplete,timestamp_precision FROM main.manual_visit_provenance')
    connection.execute('CREATE TEMP TABLE duplicate_candidates(desktop_id INTEGER,mobile_id INTEGER,delta_us INTEGER,groupable INTEGER)')
    connection.execute('CREATE INDEX temp.duplicate_desktop ON duplicate_candidates(desktop_id)')
    connection.execute('CREATE INDEX temp.duplicate_mobile ON duplicate_candidates(mobile_id)')
    cloud = ARCHIVE_DIRECTORY / 'SharedArchive.sqlite'
    union = ''
    if cloud.is_file():
        connection.execute('ATTACH DATABASE ? AS shared', (cloud.resolve().as_uri() + '?mode=ro',))
        ready = connection.execute("SELECT 1 FROM shared.sqlite_master WHERE type='table' AND name='remote'").fetchone()
        if ready:
            pairs = candidate_pairs(
                connection.execute('SELECT archive_id,url,chrome_visit_time FROM main.permanent_visits WHERE archive_id NOT IN (SELECT archive_id FROM temp.manual_visit_provenance)'),
                connection.execute("""SELECT rowid,url,chrome_visit_time,source,
                    kind='visit' AND json_extract(payload,'$.url_quality') IN ('document','shared'),
                    coalesce(json_extract(payload,'$.tab_mode'),'unknown')
                    FROM shared.remote WHERE kind IN ('search','visit') AND source='chrome_android'"""))
            connection.executemany('INSERT INTO duplicate_candidates VALUES (?,?,?,?)',pairs)
            union = ''' UNION ALL SELECT -r.rowid,r.url,
                CASE WHEN r.kind='search' THEN '[Android search] ' || r.title ELSE r.title END,
                r.domain,r.chrome_visit_time,r.visited_at_utc,NULL,NULL,NULL,NULL,r.cached_at,r.source,
                coalesce(json_extract(r.payload,'$.observed_url'),r.url),
                coalesce(json_extract(r.payload,'$.url_quality'),CASE WHEN r.kind='search' THEN 'reconstructed_search' ELSE 'legacy' END),
                coalesce(json_extract(r.payload,'$.title_source'),CASE WHEN r.kind='search' THEN 'search_query' ELSE 'unavailable' END),
                coalesce(json_extract(r.payload,'$.tab_mode'),'unknown')
                FROM shared.remote r WHERE
                    (r.kind IN ('search','visit') AND r.source IN ('chrome_android','chrome_android_share')) OR
                    (r.kind='visit' AND r.source='chrome_desktop_profile13' AND NOT EXISTS (SELECT 1 FROM main.permanent_visits p
                        WHERE p.url=r.url AND p.visited_at_utc=r.visited_at_utc))'''
    connection.execute("""CREATE TEMP VIEW archive_observations AS SELECT p.*,
        CASE WHEN m.archive_id IS NOT NULL THEN 'manual_import' ELSE 'chrome_profile13' END AS capture_source,
        p.url AS observed_url,
        CASE WHEN m.url_incomplete=1 THEN 'incomplete' WHEN m.timestamp_precision='microsecond' THEN 'user_supplied_precise' WHEN m.archive_id IS NOT NULL THEN 'user_supplied' ELSE 'desktop_history' END AS url_quality,
        CASE WHEN m.archive_id IS NOT NULL THEN 'user_supplied' ELSE 'chrome_history' END AS title_source,
        'unknown' AS tab_mode FROM main.permanent_visits p LEFT JOIN temp.manual_visit_provenance m ON m.archive_id=p.archive_id""" + union)
    grouped = 1 if duplicates!='all' else 0
    columns = ','.join('o.'+row[1] for row in connection.execute('PRAGMA main.table_info(permanent_visits)'))
    connection.execute(f'''CREATE TEMP VIEW permanent_visits AS SELECT {columns},
        CASE WHEN {grouped} AND o.archive_id>0 AND EXISTS
            (SELECT 1 FROM duplicate_candidates d WHERE d.desktop_id=o.archive_id AND d.groupable=1)
            THEN 'chrome_profile13+chrome_android' ELSE o.capture_source END AS capture_source,
        o.observed_url,o.url_quality,o.title_source,o.tab_mode,
        CASE WHEN {grouped} AND o.archive_id>0 AND EXISTS
            (SELECT 1 FROM duplicate_candidates d WHERE d.desktop_id=o.archive_id AND d.groupable=1)
            THEN 'grouped' WHEN EXISTS (SELECT 1 FROM duplicate_candidates d
                WHERE (o.archive_id>0 AND d.desktop_id=o.archive_id) OR (o.archive_id<0 AND d.mobile_id=-o.archive_id))
            THEN 'possible' ELSE '' END AS duplicate_status,
        CASE WHEN {grouped} AND o.archive_id>0 AND EXISTS
            (SELECT 1 FROM duplicate_candidates d WHERE d.desktop_id=o.archive_id AND d.groupable=1)
            THEN 2 ELSE 1 END AS observation_count
        FROM archive_observations o WHERE NOT ({grouped} AND o.archive_id<0 AND EXISTS
            (SELECT 1 FROM duplicate_candidates d WHERE d.mobile_id=-o.archive_id AND d.groupable=1))''')
    return connection


@contextmanager
def open_archive(filters=None):
    filters = filters or {}
    duplicates = 'all' if filters.get('source') in ('android', 'desktop') else filters.get('duplicates', 'group')
    with closing(_archive_connection(duplicates)) as connection:
        yield connection


def first_value(values: dict[str, list[str]], key: str) -> str:
    return values.get(key, [""])[0].strip()


def parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_time(value: str) -> clock_time | None:
    if not value:
        return None
    try:
        return clock_time.fromisoformat(value)
    except ValueError:
        return None


def filters_from_query(values: dict[str, list[str]]) -> dict[str, Any]:
    start = parse_date(first_value(values, "start"))
    end = parse_date(first_value(values, "end"))
    start_time = parse_time(first_value(values, "start_time"))
    end_time = parse_time(first_value(values, "end_time"))
    if start and end:
        start_bound = (start, start_time or clock_time.min)
        end_bound = (end, end_time or clock_time.max)
        if end_bound < start_bound:
            start, end = end, start
            start_time, end_time = end_time, start_time
    search = first_value(values, "search")[:300]
    try:
        page = max(1, int(first_value(values, "page") or "1"))
    except ValueError:
        page = 1
    try:
        limit = min(MAX_PAGE_SIZE, max(10, int(first_value(values, "limit") or "500")))
    except ValueError:
        limit = 500
    try:
        selected_hour = int(first_value(values, "hour"))
        if not 0 <= selected_hour <= 23:
            selected_hour = None
    except ValueError:
        selected_hour = None
    hour_scope = first_value(values, "hour_scope").lower()
    if hour_scope not in {"month", "week", "day", "custom"}:
        hour_scope = "custom"
    hide_noise = first_value(values, "clean") in {"1", "true", "yes"}
    return {
        "start": start,
        "end": end,
        "start_time": start_time,
        "end_time": end_time,
        "search": search,
        "hour": selected_hour,
        "hour_scope": hour_scope,
        "hide_noise": hide_noise,
        "source": first_value(values, 'source') if first_value(values, 'source') in ('desktop','android') else 'all',
        "duplicates": 'all' if first_value(values, 'source') in ('desktop', 'android') or first_value(values,'duplicates')=='all' else 'group',
        "page": page,
        "limit": limit,
    }


def local_midnight_to_utc_iso(day: date) -> str:
    return datetime.combine(day, datetime.min.time(), SRI_LANKA_TIMEZONE).astimezone(
        timezone.utc
    ).isoformat()


def local_date_time_to_utc_iso(day: date, at_time: clock_time) -> str:
    return datetime.combine(day, at_time, SRI_LANKA_TIMEZONE).astimezone(
        timezone.utc
    ).isoformat()


def build_where(filters: dict[str, Any]) -> tuple[str, list[str]]:
    clauses: list[str] = []
    parameters: list[str] = []
    if filters.get('source') == 'android':
        clauses.append("capture_source IN ('chrome_android','chrome_android_share','chrome_profile13+chrome_android')")
    elif filters.get('source') == 'desktop':
        clauses.append("capture_source IN ('chrome_profile13','chrome_profile13+chrome_android')")
    if filters["start"]:
        clauses.append("visited_at_utc >= ?")
        parameters.append(
            local_date_time_to_utc_iso(
                filters["start"], filters.get("start_time") or clock_time.min
            )
        )
    if filters["end"]:
        clauses.append("visited_at_utc < ?")
        if filters.get("end_time"):
            # The ending minute is exclusive: 14:23 to 14:24 is one minute.
            ending = datetime.combine(
                filters["end"], filters.get("end_time"), SRI_LANKA_TIMEZONE
            )
            parameters.append(ending.astimezone(timezone.utc).isoformat())
        else:
            parameters.append(
                local_midnight_to_utc_iso(filters["end"] + timedelta(days=1))
            )
    if filters["search"]:
        clauses.append("(url LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR domain LIKE ? ESCAPE '\\')")
        search = (
            filters["search"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        parameters.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    if filters.get("hour") is not None:
        clauses.append(
            "CAST(strftime('%H', visited_at_utc, '+5 hours', '+30 minutes') AS INTEGER) = ?"
        )
        parameters.append(filters["hour"])
    if filters.get("hide_noise"):
        clauses.append(
            "(LOWER(domain) NOT LIKE ? AND LOWER(domain) NOT LIKE ? AND LOWER(domain) NOT LIKE ? "
            "AND LOWER(domain) NOT LIKE ? AND LOWER(domain) NOT LIKE ? "
            "AND LOWER(url) NOT LIKE ? AND LOWER(url) NOT LIKE ? AND LOWER(url) NOT LIKE ? AND LOWER(url) NOT LIKE ?)"
        )
        parameters.extend(
            [
                "%accounts.google.com%",
                "%consent.google.%",
                "%googleadservices.com%",
                "%doubleclick.net%",
                "%googlesyndication.com%",
                "%google.com/url?%",
                "%google.com/aclk?%",
                "about:blank%",
                "chrome://%",
            ]
        )
    if not clauses:
        return "", parameters
    return " WHERE " + " AND ".join(clauses), parameters


def sri_lanka_date(utc_iso: str) -> date:
    return datetime.fromisoformat(utc_iso).astimezone(SRI_LANKA_TIMEZONE).date()


def sri_lanka_iso(utc_iso: str | None) -> str | None:
    if not utc_iso:
        return None
    return datetime.fromisoformat(utc_iso).astimezone(SRI_LANKA_TIMEZONE).isoformat()


def extract_search_query(raw_url: str) -> tuple[str, str] | None:
    """Return a user-entered search query and its source website when identifiable."""

    strict = parse_search(raw_url)
    if strict: return strict
    try:
        parsed = urlparse(raw_url)
        domain = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        values = parse_qs(parsed.query, keep_blank_values=False)
    except ValueError:
        return None

    is_known_search = (
        ("google." in domain and path.startswith("/search"))
        or ("bing.com" in domain and path.startswith("/search"))
        or ("duckduckgo.com" in domain)
        or ("yahoo." in domain and path.startswith("/search"))
        or ("brave.com" in domain and "/search" in path)
        or ("yandex." in domain and path.startswith("/search"))
        or ("youtube.com" in domain and path == "/results")
        or ("amazon." in domain and path.startswith("/s"))
        or ("ebay." in domain and path.startswith("/sch"))
        or ("reddit.com" in domain and path.startswith("/search"))
        or ("wikipedia.org" in domain and "search" in path)
    )
    is_generic_search = any(marker in path for marker in ("/search", "/find", "/results", "/query"))
    if not (is_known_search or is_generic_search):
        return None

    for key in SEARCH_QUERY_KEYS:
        candidate = values.get(key, [""])[0]
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if len(candidate) >= 2 and len(candidate) <= 200:
            return candidate, domain
    return None


def normalise_nsfw_text(value: str) -> str:
    """Normalise URL/title text without losing word boundaries for matching."""

    return re.sub(r"\s+", " ", unquote(value).casefold().replace("_", " ").replace("-", " ")).strip()


def contains_nsfw_term(text: str, term: str) -> bool:
    """Match whole terms or phrases, never a fragment inside an ordinary word."""

    cleaned_term = normalise_nsfw_text(term)
    if not cleaned_term:
        return False
    return re.search(r"(?<!\w)" + re.escape(cleaned_term) + r"(?!\w)", normalise_nsfw_text(text)) is not None


def has_nsfw_domain_marker(domain: str, marker: str) -> bool:
    """Match a known site name as a hostname label, not an arbitrary substring."""

    labels = re.split(r"[.-]", domain.casefold().strip("."))
    return marker.casefold() in labels


def any_nsfw_term(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    return any(contains_nsfw_term(text, term) for term in terms)


def is_adult_search_query(query: str) -> bool:
    """Flag likely adult searches using conservative, explicit search terms."""

    return any_nsfw_term(query, ADULT_SEARCH_TERMS)


def nsfw_search_category(query: str) -> tuple[str, str]:
    """Assign a transparent local category to a matched search phrase."""

    for key, label, terms in NSFW_SEARCH_CATEGORIES:
        if any_nsfw_term(query, terms):
            return key, label
    return "other", "Other explicit search"


def nsfw_search_source_group(domain: str) -> tuple[str, str]:
    """Separate Google searches from searches performed inside other websites."""

    return ("google", "Google") if "google." in domain.casefold() else ("site", "Inside a website")


def nsfw_source_rows(counts: Counter[str]) -> list[dict[str, Any]]:
    return [
        {
            "domain": domain,
            "count": count,
            "group": nsfw_search_source_group(domain)[0],
            "group_label": nsfw_search_source_group(domain)[1],
        }
        for domain, count in counts.most_common()
    ]


def adult_direct_visit_reason(raw_url: str, title: str | None, domain: str | None) -> str | None:
    """Return a cautious local classification for an explicit direct visit."""

    try:
        parsed = urlparse(raw_url)
        host = (domain or parsed.hostname or "").casefold()
        path = unquote(parsed.path).casefold()
    except ValueError:
        host = (domain or "").casefold()
        path = ""
    title_text = (title or "").casefold()
    if any(has_nsfw_domain_marker(host, marker) for marker in ADULT_DOMAIN_MARKERS):
        return "Known adult website pattern"
    direct_text = " ".join((path, title_text))
    if any_nsfw_term(direct_text, ADULT_DIRECT_TERMS):
        return "Explicit website or page signal"
    return None


def default_nsfw_rules() -> dict[str, list[str]]:
    return {key: [] for key in NSFW_RULE_KEYS}


def load_nsfw_rules() -> dict[str, list[str]]:
    """Load optional local classification rules without touching the archive."""

    try:
        raw = json.loads(NSFW_RULES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_nsfw_rules()
    if not isinstance(raw, dict):
        return default_nsfw_rules()
    rules = default_nsfw_rules()
    for key in NSFW_RULE_KEYS:
        values = raw.get(key, [])
        if isinstance(values, list):
            rules[key] = [str(value) for value in values if isinstance(value, str)][:100]
    return rules


def normalise_nsfw_rules(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        raise ValueError("Rules must be a JSON object.")
    rules = default_nsfw_rules()
    for key in NSFW_RULE_KEYS:
        values = raw.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"{key} must be a list.")
        cleaned: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            value = re.sub(r"\s+", " ", value.casefold()).strip().lstrip(".")
            if value and len(value) <= 160 and value not in cleaned:
                cleaned.append(value)
        rules[key] = cleaned[:100]
    return rules


def save_nsfw_rules(raw: Any) -> dict[str, list[str]]:
    rules = normalise_nsfw_rules(raw)
    temporary = NSFW_RULES_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, NSFW_RULES_FILE)
    return rules


def nsfw_match(row: sqlite3.Row, rules: dict[str, list[str]]) -> dict[str, Any] | None:
    """Return a transparent potential-match classification, never a certainty claim."""

    raw_url = row["url"]
    title = row["title"] or ""
    domain = (row["domain"] or urlparse(raw_url).hostname or "").casefold()
    extracted = extract_search_query(raw_url)
    query = extracted[0] if extracted else ""
    searchable = " ".join((domain, unquote(raw_url).casefold(), title.casefold(), query.casefold()))
    if any(value and (domain == value or domain.endswith("." + value)) for value in rules["ignore_domains"]):
        return None
    if any(value and contains_nsfw_term(searchable, value) for value in rules["ignore_terms"]):
        return None
    if any(value and (domain == value or domain.endswith("." + value)) for value in rules["include_domains"]):
        return {"type": "Search" if extracted else "Direct visit", "reason": "Custom included website", "confidence": "High", "query": query, "score": 100}
    if any(value and contains_nsfw_term(searchable, value) for value in rules["include_terms"]):
        return {"type": "Search" if extracted else "Direct visit", "reason": "Custom included term", "confidence": "High", "query": query, "score": 100}
    if extracted and any(has_nsfw_domain_marker(domain, marker) for marker in ADULT_DOMAIN_MARKERS):
        return {"type": "Search", "reason": "Search inside known adult website", "confidence": "High", "query": query, "score": 95}
    if extracted and is_adult_search_query(query):
        return {"type": "Search", "reason": "Explicit search term", "confidence": "Medium", "query": query, "score": 65}
    if any(has_nsfw_domain_marker(domain, marker) for marker in ADULT_DOMAIN_MARKERS):
        return {"type": "Direct visit", "reason": "Known adult website pattern", "confidence": "High", "query": "", "score": 90}
    if adult_direct_visit_reason(raw_url, title, domain):
        return {"type": "Direct visit", "reason": "Explicit website or page signal", "confidence": "Medium", "query": "", "score": 60}
    return None


def query_cluster(query: str) -> str:
    words = re.findall(r"[\w']+", query.casefold())
    meaningful = [word for word in words if len(word) > 1 and word not in NSFW_STOP_WORDS]
    return " ".join(meaningful[:5]) or query.casefold()


def nsfw_analytics(connection: sqlite3.Connection, where: str, parameters: list[str]) -> dict[str, Any]:
    rules = load_nsfw_rules()
    rows = connection.execute(
        "SELECT visited_at_utc, title, url, domain, visit_duration FROM permanent_visits" + where + " ORDER BY visited_at_utc ASC",
        parameters,
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        match = nsfw_match(row, rules)
        if match:
            event = dict(row)
            event.update(match)
            events.append(event)

    by_day: Counter[str] = Counter()
    heatmap: Counter[tuple[int, int]] = Counter()
    sources: Counter[str] = Counter()
    source_first: dict[str, str] = {}
    clusters: Counter[str] = Counter()
    cluster_url: dict[str, str] = {}
    reasons: Counter[str] = Counter()
    confidence: Counter[str] = Counter()
    search_category_counts: Counter[str] = Counter()
    search_category_labels: dict[str, str] = {}
    search_category_domains: defaultdict[str, Counter[str]] = defaultdict(Counter)
    search_category_queries: defaultdict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    explorer_queries: dict[str, dict[str, Any]] = {}
    explorer_clusters: dict[str, dict[str, Any]] = {}
    explorer_keywords: dict[str, dict[str, Any]] = {}
    for event in events:
        moment = datetime.fromisoformat(event["visited_at_utc"]).astimezone(SRI_LANKA_TIMEZONE)
        by_day[moment.date().isoformat()] += 1
        heatmap[(moment.weekday(), moment.hour)] += 1
        domain = event["domain"] or "Unknown website"
        sources[domain] += 1
        source_first.setdefault(domain, event["visited_at_utc"])
        reasons[event["reason"]] += 1
        confidence[event["confidence"]] += 1
        if event["query"]:
            category_key, category_label = nsfw_search_category(event["query"])
            event["search_category"] = category_key
            search_category_counts[category_key] += 1
            search_category_labels[category_key] = category_label
            search_category_domains[category_key][domain] += 1
            query_key = re.sub(r"\s+", " ", event["query"].casefold()).strip()
            query_summary = search_category_queries[category_key].setdefault(
                query_key,
                {
                    "query": event["query"],
                    "count": 0,
                    "url": event["url"],
                    "source": domain,
                    "latest": event["visited_at_utc"],
                    "source_counts": Counter(),
                },
            )
            query_summary["count"] += 1
            query_summary["source_counts"][domain] += 1
            if event["visited_at_utc"] > query_summary["latest"]:
                query_summary["latest"] = event["visited_at_utc"]
                query_summary["url"] = event["url"]
                query_summary["source"] = domain
            explorer_query = explorer_queries.setdefault(
                query_key,
                {
                    "query": event["query"],
                    "count": 0,
                    "url": event["url"],
                    "latest": event["visited_at_utc"],
                    "sources": Counter(),
                    "source_urls": {},
                    "source_latest": {},
                },
            )
            explorer_query["count"] += 1
            explorer_query["sources"][domain] += 1
            if event["visited_at_utc"] >= explorer_query["source_latest"].get(domain, ""):
                explorer_query["source_latest"][domain] = event["visited_at_utc"]
                explorer_query["source_urls"][domain] = event["url"]
            if event["visited_at_utc"] > explorer_query["latest"]:
                explorer_query["latest"] = event["visited_at_utc"]
                explorer_query["url"] = event["url"]
            key = query_cluster(event["query"])
            clusters[key] += 1
            cluster_url.setdefault(key, event["url"])
            explorer_cluster = explorer_clusters.setdefault(
                key,
                {"cluster": key, "count": 0, "queries": Counter(), "sources": Counter()},
            )
            explorer_cluster["count"] += 1
            explorer_cluster["queries"][event["query"]] += 1
            explorer_cluster["sources"][domain] += 1
            words = {
                word
                for word in re.findall(r"[\w']+", event["query"].casefold())
                if len(word) > 1 and word not in NSFW_STOP_WORDS
            }
            for word in words:
                keyword = explorer_keywords.setdefault(
                    word,
                    {"keyword": word, "count": 0, "queries": Counter(), "sources": Counter()},
                )
                keyword["count"] += 1
                keyword["queries"][event["query"]] += 1
                keyword["sources"][domain] += 1

    sessions: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    for event in events:
        if group:
            previous = datetime.fromisoformat(group[-1]["visited_at_utc"])
            current = datetime.fromisoformat(event["visited_at_utc"])
            if current - previous > timedelta(minutes=30):
                sessions.append(group)
                group = []
        group.append(event)
    if group:
        sessions.append(group)
    rendered_sessions = []
    nsfw_daily_active_seconds: Counter[date] = Counter()
    nsfw_total_active_seconds = 0
    for group in sorted(sessions, key=lambda item: item[-1]["visited_at_utc"], reverse=True)[:10]:
        start = datetime.fromisoformat(group[0]["visited_at_utc"])
        end = datetime.fromisoformat(group[-1]["visited_at_utc"])
        local_group = [(item, datetime.fromisoformat(item["visited_at_utc"]).astimezone(SRI_LANKA_TIMEZONE)) for item in group]
        merged = estimated_active_intervals(local_group)
        active_seconds = round(sum((ending - starting).total_seconds() for starting, ending in merged))
        nsfw_total_active_seconds += active_seconds
        for interval_start, interval_end in merged:
            cursor = interval_start
            while cursor < interval_end:
                next_midnight = datetime.combine(cursor.date() + timedelta(days=1), clock_time.min, SRI_LANKA_TIMEZONE)
                segment_end = min(interval_end, next_midnight)
                nsfw_daily_active_seconds[cursor.date()] += round((segment_end - cursor).total_seconds())
                cursor = segment_end
        rendered_sessions.append({"start": group[0]["visited_at_utc"], "end": group[-1]["visited_at_utc"], "events": len(group), "minutes": round((end - start).total_seconds() / 60), "active_seconds": active_seconds, "top_site": Counter(item["domain"] or "Unknown website" for item in group).most_common(1)[0][0]})

    # Include sessions outside the ten rendered cards in the time totals.
    for group in sorted(sessions, key=lambda item: item[-1]["visited_at_utc"], reverse=True)[10:]:
        local_group = [(item, datetime.fromisoformat(item["visited_at_utc"]).astimezone(SRI_LANKA_TIMEZONE)) for item in group]
        merged = estimated_active_intervals(local_group)
        nsfw_total_active_seconds += round(sum((ending - starting).total_seconds() for starting, ending in merged))
        for interval_start, interval_end in merged:
            cursor = interval_start
            while cursor < interval_end:
                next_midnight = datetime.combine(cursor.date() + timedelta(days=1), clock_time.min, SRI_LANKA_TIMEZONE)
                segment_end = min(interval_end, next_midnight)
                nsfw_daily_active_seconds[cursor.date()] += round((segment_end - cursor).total_seconds())
                cursor = segment_end

    event_dates = sorted(by_day)
    end_day = date.fromisoformat(event_dates[-1]) if event_dates else date.today()
    recent_start, prior_start = end_day - timedelta(days=6), end_day - timedelta(days=13)
    recent = sum(count for day, count in by_day.items() if recent_start <= date.fromisoformat(day) <= end_day)
    prior = sum(count for day, count in by_day.items() if prior_start <= date.fromisoformat(day) < recent_start)
    change = None if prior == 0 else round(((recent - prior) / prior) * 100)
    return {
        "events": len(events), "searches": sum(1 for event in events if event["type"] == "Search"), "direct": sum(1 for event in events if event["type"] == "Direct visit"),
        "high_confidence": confidence["High"], "timeline": [{"date": day, "count": count} for day, count in sorted(by_day.items())[-42:]],
        "heatmap": [{"weekday": weekday, "hour": hour, "count": heatmap[(weekday, hour)]} for weekday in range(7) for hour in range(24)],
        "sources": [{"domain": domain, "count": count, "first_seen": source_first[domain]} for domain, count in sources.most_common(12)],
        "clusters": [{"query": key, "count": count, "url": cluster_url[key]} for key, count in clusters.most_common(12)],
        "search_explorer": {
            "events": sum(item["count"] for item in explorer_queries.values()),
            "unique_queries": len(explorer_queries),
            "unique_clusters": len(explorer_clusters),
            "unique_keywords": len(explorer_keywords),
            "sources": len({source for item in explorer_queries.values() for source in item["sources"]}),
            "queries": [
                {
                    "query": item["query"],
                    "count": item["count"],
                    "url": item["url"],
                    "latest": item["latest"],
                    "sources": [
                        {
                            **source,
                            "url": item["source_urls"][source["domain"]],
                            "latest": item["source_latest"][source["domain"]],
                        }
                        for source in nsfw_source_rows(item["sources"])
                    ],
                }
                for item in sorted(explorer_queries.values(), key=lambda item: (-item["count"], item["query"].casefold()))
            ],
            "clusters": [
                {
                    "cluster": item["cluster"],
                    "count": item["count"],
                    "queries": [{"query": query, "count": count} for query, count in item["queries"].most_common()],
                    "sources": nsfw_source_rows(item["sources"]),
                }
                for item in sorted(explorer_clusters.values(), key=lambda item: (-item["count"], item["cluster"]))
            ],
            "keywords": [
                {
                    "keyword": item["keyword"],
                    "count": item["count"],
                    "queries": [{"query": query, "count": count} for query, count in item["queries"].most_common()],
                    "sources": nsfw_source_rows(item["sources"]),
                }
                for item in sorted(explorer_keywords.values(), key=lambda item: (-item["count"], item["keyword"]))
            ],
        },
        "search_categories": [
            {
                "key": key,
                "label": search_category_labels[key],
                "count": count,
                "unique_queries": len(search_category_queries[key]),
                "websites": len(search_category_domains[key]),
                "queries": [
                    {
                        "query": item["query"],
                        "count": item["count"],
                        "url": item["url"],
                        "latest": item["latest"],
                        "sources": [
                            {
                                "domain": domain,
                                "count": source_count,
                                "group": nsfw_search_source_group(domain)[0],
                                "group_label": nsfw_search_source_group(domain)[1],
                            }
                            for domain, source_count in sorted(
                                item["source_counts"].items(),
                                key=lambda source: (-source[1], source[0]),
                            )
                        ],
                    }
                    for item in sorted(
                        search_category_queries[key].values(),
                        key=lambda item: (-item["count"], item["query"].casefold()),
                    )
                ],
            }
            for key, count in search_category_counts.most_common()
        ],
        "reasons": [{"label": label, "count": count} for label, count in reasons.most_common()],
        "confidence": [{"label": label, "count": confidence[label]} for label in ("High", "Medium", "Low") if confidence[label]],
        "sessions": rendered_sessions,
        "browsing_time": {
            "total_seconds": nsfw_total_active_seconds,
            "active_days": len(by_day),
            "average_daily_seconds": round(nsfw_total_active_seconds / max(1, len(by_day))),
            "daily": [
                {"date": day, "seconds": nsfw_daily_active_seconds[date.fromisoformat(day)], "visits": by_day[day]}
                for day in sorted(by_day)[-42:]
            ],
        },
        "new_sites": [{"domain": domain, "count": count, "first_seen": source_first[domain]} for domain, count in sorted(sources.items(), key=lambda item: source_first[item[0]], reverse=True)[:8]],
        "comparison": {"recent": recent, "prior": prior, "change": change, "label": f"{recent_start.isoformat()} to {end_day.isoformat()}"},
        "events_detail": events,
    }


def search_analytics(
    connection: sqlite3.Connection,
    where: str,
    parameters: list[str],
) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT url, title, domain FROM permanent_visits" + where + " ORDER BY chrome_visit_time DESC",
        parameters,
    ).fetchall()
    query_counts: Counter[str] = Counter()
    query_display: dict[str, str] = {}
    query_websites: dict[str, set[str]] = {}
    query_urls: dict[str, str] = {}
    source_counts: Counter[str] = Counter()
    adult_counts: Counter[str] = Counter()
    adult_display: dict[str, str] = {}
    adult_websites: dict[str, set[str]] = {}
    adult_urls: dict[str, str] = {}
    adult_sources: Counter[str] = Counter()
    adult_direct_sources: Counter[str] = Counter()
    adult_events = 0
    adult_direct_events = 0

    for row in rows:
        extracted = extract_search_query(row["url"])
        if extracted:
            query, domain = extracted
            key = query.casefold()
            query_counts[key] += 1
            query_display.setdefault(key, query)
            query_websites.setdefault(key, set()).add(domain)
            query_urls.setdefault(key, row["url"])
            source_counts[domain] += 1
            if is_adult_search_query(query):
                adult_key = "search:" + key
                adult_counts[adult_key] += 1
                adult_display.setdefault(adult_key, "Search · " + query)
                adult_websites.setdefault(adult_key, set()).add(domain)
                adult_urls.setdefault(adult_key, row["url"])
                adult_sources[domain] += 1
                adult_events += 1
            continue

        reason = adult_direct_visit_reason(row["url"], row["title"], row["domain"])
        if not reason:
            continue
        domain = row["domain"] or (urlparse(row["url"]).hostname or "")
        adult_key = "site:" + domain.casefold()
        adult_counts[adult_key] += 1
        adult_display.setdefault(adult_key, "Website · " + (domain or "Unknown website"))
        adult_websites.setdefault(adult_key, set()).add(reason)
        adult_urls.setdefault(adult_key, row["url"])
        adult_sources[domain] += 1
        adult_direct_sources[domain] += 1
        adult_events += 1
        adult_direct_events += 1

    top_queries = []
    for key, count in query_counts.most_common(12):
        websites = sorted(query_websites[key])
        top_queries.append(
            {
                "query": query_display[key],
                "count": count,
                "websites": ", ".join(websites[:3]),
                "url": query_urls[key],
            }
        )
    adult_queries = []
    for key, count in adult_counts.most_common(12):
        adult_queries.append(
            {
                "query": adult_display[key],
                "count": count,
                "websites": ", ".join(sorted(adult_websites[key])[:3]),
                "url": adult_urls[key],
            }
        )
    return {
        "events": sum(query_counts.values()),
        "unique_queries": len(query_counts),
        "top_queries": top_queries,
        "sources": [
            {"domain": domain, "count": count}
            for domain, count in source_counts.most_common(10)
        ],
        "adult": {
            "events": adult_events,
            "unique_queries": len(adult_counts),
            "direct_events": adult_direct_events,
            "top_queries": adult_queries,
            "sources": [
                {"domain": domain, "count": count}
                for domain, count in (adult_direct_sources or adult_sources).most_common(10)
            ],
        },
    }


def is_noise_visit(raw_url: str, domain: str | None) -> bool:
    value = raw_url.casefold()
    host = (domain or "").casefold()
    return any(marker in host for marker in NOISE_DOMAIN_MARKERS) or any(
        marker in value for marker in NOISE_URL_MARKERS
    )


def visit_category(raw_url: str, domain: str | None) -> str:
    host = (domain or "").casefold()
    value = (host + " " + raw_url.casefold())
    if is_noise_visit(raw_url, domain):
        return "Noise"
    if extract_search_query(raw_url):
        return "Search"
    if any(marker in host for marker in ("chatgpt", "openai", "claude", "gemini", "perplexity", "copilot")):
        return "AI tools"
    if any(marker in host for marker in ("youtube", "netflix", "twitch", "vimeo", "spotify")):
        return "Video & audio"
    if any(marker in host for marker in ("facebook", "instagram", "tiktok", "twitter", "x.com", "reddit", "discord")):
        return "Social"
    if any(marker in host for marker in ("amazon", "ebay", "daraz", "aliexpress", "etsy")):
        return "Shopping"
    if any(marker in value for marker in ("coursera", "udemy", "khanacademy", ".edu", "education", "school", "lms")):
        return "Learning"
    if any(marker in host for marker in ("gmail", "outlook", "mail.", "slack", "zoom", "meet.google")):
        return "Communication"
    if any(marker in value for marker in ("news", "bbc", "cnn", "reuters", "guardian")):
        return "News"
    if any(marker in value for marker in ("bank", "paypal", "payhere", "visa", "mastercard")):
        return "Finance"
    return "Other"


def estimated_active_intervals(
    items: list[tuple[Any, datetime]],
) -> list[list[datetime]]:
    """Estimate active intervals consistently and merge overlapping browser tabs."""

    intervals: list[tuple[datetime, datetime]] = []
    for index, (row, started_at) in enumerate(items):
        recorded_seconds = max(0.0, float(row["visit_duration"] or 0) / 1_000_000)
        if recorded_seconds:
            estimated_seconds = min(recorded_seconds, 30 * 60)
        elif index + 1 < len(items):
            estimated_seconds = min(
                max(0.0, (items[index + 1][1] - started_at).total_seconds()),
                5 * 60,
            )
        else:
            estimated_seconds = 0
        if estimated_seconds:
            intervals.append((started_at, started_at + timedelta(seconds=estimated_seconds)))

    merged: list[list[datetime]] = []
    for interval_start, interval_end in intervals:
        if merged and interval_start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], interval_end)
        else:
            merged.append([interval_start, interval_end])
    return merged


def advanced_insights(
    connection: sqlite3.Connection,
    where: str,
    parameters: list[str],
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT visited_at_utc, chrome_visit_time, title, url, domain, visit_duration
        FROM permanent_visits
        """
        + where
        + " ORDER BY chrome_visit_time ASC, archive_id ASC",
        parameters,
    ).fetchall()
    local_rows: list[tuple[sqlite3.Row, datetime]] = []
    calendar_counts: Counter[date] = Counter()
    category_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    for row in rows:
        if not row["visited_at_utc"]:
            continue
        local_time = datetime.fromisoformat(row["visited_at_utc"]).astimezone(
            SRI_LANKA_TIMEZONE
        )
        local_rows.append((row, local_time))
        calendar_counts[local_time.date()] += 1
        category_counts[visit_category(row["url"], row["domain"])] += 1
        if row["domain"]:
            domain_counts[row["domain"]] += 1

    sessions: list[list[tuple[sqlite3.Row, datetime]]] = []
    current_session: list[tuple[sqlite3.Row, datetime]] = []
    for item in local_rows:
        if current_session and item[1] - current_session[-1][1] > timedelta(minutes=30):
            sessions.append(current_session)
            current_session = []
        current_session.append(item)
    if current_session:
        sessions.append(current_session)

    session_cards = []
    daily_active_seconds: Counter[date] = Counter()
    total_active_seconds = 0
    for session in sessions:
        if not session:
            continue
        domain_counter = Counter(row["domain"] for row, _ in session if row["domain"])
        merged = estimated_active_intervals(session)
        active_seconds = round(sum((ending - starting).total_seconds() for starting, ending in merged))
        total_active_seconds += active_seconds
        for interval_start, interval_end in merged:
            cursor = interval_start
            while cursor < interval_end:
                next_midnight = datetime.combine(cursor.date() + timedelta(days=1), clock_time.min, SRI_LANKA_TIMEZONE)
                segment_end = min(interval_end, next_midnight)
                daily_active_seconds[cursor.date()] += round((segment_end - cursor).total_seconds())
                cursor = segment_end
        session_end = max([session[-1][1], *[ending for _, ending in merged]])
        span_seconds = max(0, round((session_end - session[0][1]).total_seconds()))
        session_cards.append(
            {
                "start": session[0][0]["visited_at_utc"],
                "last_visit": session[-1][0]["visited_at_utc"],
                "end": session_end.astimezone(timezone.utc).isoformat(),
                "span_seconds": span_seconds,
                "active_seconds": active_seconds,
                "visits": len(session),
                "top_domain": domain_counter.most_common(1)[0][0] if domain_counter else "—",
            }
        )
    session_cards.sort(key=lambda item: item["start"], reverse=True)

    journeys = []
    for index, (row, local_time) in enumerate(local_rows):
        extracted = extract_search_query(row["url"])
        if not extracted:
            continue
        query, source = extracted
        results = []
        for next_row, next_time in local_rows[index + 1 : index + 9]:
            if next_time - local_time > timedelta(minutes=30):
                break
            if extract_search_query(next_row["url"]):
                break
            results.append(
                {
                    "title": next_row["title"] or next_row["domain"] or "Untitled page",
                    "url": next_row["url"],
                    "domain": next_row["domain"] or "—",
                }
            )
        if results:
            journeys.append(
                {
                    "query": query,
                    "source": source,
                    "visited_at": row["visited_at_utc"],
                    "url": row["url"],
                    "results": results[:5],
                }
            )
    journeys = journeys[-8:][::-1]

    repeated: dict[str, dict[str, Any]] = {}
    for row, _ in local_rows:
        key = row["url"]
        entry = repeated.setdefault(
            key,
            {
                "url": key,
                "title": row["title"] or row["domain"] or "Untitled page",
                "domain": row["domain"] or "—",
                "count": 0,
            },
        )
        entry["count"] += 1
    revisited = sorted(
        (item for item in repeated.values() if item["count"] >= 2),
        key=lambda item: (-item["count"], item["title"]),
    )[:10]

    latest_day = max(calendar_counts) if calendar_counts else datetime.now(SRI_LANKA_TIMEZONE).date()
    report_start = latest_day - timedelta(days=6)
    report_rows = [(row, local_time) for row, local_time in local_rows if local_time.date() >= report_start]
    report_domains = Counter(row["domain"] for row, _ in report_rows if row["domain"])
    report_categories = Counter(visit_category(row["url"], row["domain"]) for row, _ in report_rows)
    report_hours = Counter(local_time.hour for _, local_time in report_rows)
    report_days = Counter(local_time.strftime("%a") for _, local_time in report_rows)

    return {
        "calendar": [
            {"date": day.isoformat(), "count": count}
            for day, count in sorted(calendar_counts.items())[-42:]
        ],
        "sessions": session_cards,
        "browsing_time": {
            "total_seconds": total_active_seconds,
            "active_days": len(calendar_counts),
            "average_daily_seconds": round(total_active_seconds / max(1, len(calendar_counts))),
            "daily": [
                {"date": day.isoformat(), "seconds": daily_active_seconds[day], "visits": calendar_counts[day]}
                for day in sorted(calendar_counts)[-42:]
            ],
        },
        "journeys": journeys,
        "revisited": revisited,
        "categories": [
            {"label": label, "count": count}
            for label, count in category_counts.most_common(8)
            if label != "Noise"
        ],
        "weekly_report": {
            "label": f"{report_start.strftime('%d %b')} – {latest_day.strftime('%d %b %Y')}",
            "visits": len(report_rows),
            "top_domain": report_domains.most_common(1)[0][0] if report_domains else "—",
            "top_category": report_categories.most_common(1)[0][0] if report_categories else "—",
            "busiest_hour": f"{report_hours.most_common(1)[0][0]:02d}:00" if report_hours else "—",
            "busiest_day": report_days.most_common(1)[0][0] if report_days else "—",
        },
    }


def activity_patterns(
    connection: sqlite3.Connection,
    where: str,
    parameters: list[str],
    filters: dict[str, Any],
) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT visited_at_utc FROM permanent_visits" + where,
        parameters,
    ).fetchall()
    hour_counts: Counter[int] = Counter()
    weekday_counts: Counter[int] = Counter()
    for row in rows:
        if not row["visited_at_utc"]:
            continue
        local_time = datetime.fromisoformat(row["visited_at_utc"]).astimezone(
            SRI_LANKA_TIMEZONE
        )
        hour_counts[local_time.hour] += 1
        weekday_counts[local_time.weekday()] += 1

    scope = filters.get("hour_scope", "custom")
    if filters.get("start") and filters.get("end"):
        period_days = max(1, (filters["end"] - filters["start"]).days + 1)
    elif rows:
        local_days = [
            sri_lanka_date(row["visited_at_utc"])
            for row in rows
            if row["visited_at_utc"]
        ]
        period_days = max(1, (max(local_days) - min(local_days)).days + 1) if local_days else 1
    else:
        period_days = 1

    is_average = scope in {"month", "week"}
    if scope == "month":
        description = f"Average visits per day · {period_days} day period · click an hour to filter every panel"
    elif scope == "week":
        description = f"Average visits per day · {period_days} day week · click an hour to filter every panel"
    elif scope == "day":
        description = "Visits on this date · click an hour to filter every panel"
    else:
        description = "Visits in the selected range · click an hour to filter every panel"

    def display_value(count: int) -> str:
        if not is_average:
            return str(count)
        average = count / period_days
        return f"{average:.1f}".rstrip("0").rstrip(".")

    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    return {
        "hours": [
            {
                "label": f"{hour:02d}:00",
                "short_label": f"{hour:02d}",
                "hour": hour,
                "count": hour_counts[hour],
                "value": round(hour_counts[hour] / period_days, 2) if is_average else hour_counts[hour],
                "display": display_value(hour_counts[hour]),
            }
            for hour in range(24)
        ],
        "weekdays": [
            {"label": weekdays[day], "count": weekday_counts[day]}
            for day in range(7)
        ],
        "description": description,
        "selected_hour": filters.get("hour"),
    }


def collector_mutex_is_open() -> bool:
    """Check the collector's existing Windows single-instance mutex."""

    if os.name != "nt":
        return False
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenMutexW(
        synchronize, False, COLLECTOR_MUTEX_NAME
    )
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def collector_process_ids() -> list[int]:
    """Return only processes whose command line names this exact collector."""

    if os.name != "nt":
        return []
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = (
        "$path='C:\\ChromePermanentHistory\\Profile13\\history_collector.py';"
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" |"
        "Where-Object {$_.CommandLine -like ('*'+$path+'*')} |"
        "ForEach-Object {$_.ProcessId}"
    )
    try:
        completed = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return [int(value) for value in completed.stdout.splitlines() if value.strip().isdigit()]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def recent_collector_log(limit: int = 24) -> list[str]:
    if not COLLECTOR_LOG.is_file():
        return []
    with COLLECTOR_LOG.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip() for line in handle.readlines()[-limit:] if line.strip()]


def launch_collector() -> None:
    if collector_mutex_is_open():
        return
    COLLECTOR_STOP_REQUEST.unlink(missing_ok=True)
    subprocess.Popen(
        [sys.executable, str(COLLECTOR_SCRIPT)],
        cwd=str(ARCHIVE_DIRECTORY),
        close_fds=True,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        ),
    )


def wait_for_collector(running: bool, timeout: float = 12.0) -> bool:
    deadline = time_module.monotonic() + timeout
    while time_module.monotonic() < deadline:
        if collector_mutex_is_open() is running:
            return True
        time_module.sleep(0.2)
    return collector_mutex_is_open() is running


def collector_control(action: str) -> dict[str, Any]:
    messages = {
        "check": "An immediate archive scan was requested.",
        "start": "The collector was started.",
        "restart": "The collector was restarted safely.",
        "pause": "Collection is paused. Existing archived history remains available.",
        "resume": "Collection resumed and an immediate scan was requested.",
        "stop": "The collector was stopped. Automatic collection will resume at the next Windows sign-in unless started sooner.",
        "open_folder": "The private archive folder was opened.",
    }
    if action not in messages:
        raise ValueError("Unknown collector control.")

    running = collector_mutex_is_open()
    if action == "check":
        if COLLECTOR_PAUSE_REQUEST.is_file():
            raise ValueError("Resume the collector before requesting a scan.")
        if running:
            COLLECTOR_SCAN_REQUEST.touch()
        else:
            launch_collector()
            if not wait_for_collector(True):
                raise OSError("The collector did not start.")
    elif action == "start":
        COLLECTOR_PAUSE_REQUEST.unlink(missing_ok=True)
        if running:
            COLLECTOR_SCAN_REQUEST.touch()
            messages[action] = "The collector was already running; an immediate scan was requested."
        else:
            launch_collector()
            if not wait_for_collector(True):
                raise OSError("The collector did not start.")
    elif action == "restart":
        if running:
            COLLECTOR_STOP_REQUEST.touch()
            if not wait_for_collector(False):
                raise OSError("The collector is busy and could not restart safely yet.")
        launch_collector()
        if not wait_for_collector(True):
            raise OSError("The collector did not restart.")
    elif action == "pause":
        if not running:
            raise ValueError("The collector is not running. Start it before pausing.")
        COLLECTOR_PAUSE_REQUEST.touch()
    elif action == "resume":
        COLLECTOR_PAUSE_REQUEST.unlink(missing_ok=True)
        if running:
            COLLECTOR_SCAN_REQUEST.touch()
        else:
            launch_collector()
            if not wait_for_collector(True):
                raise OSError("The collector did not resume.")
    elif action == "stop":
        COLLECTOR_PAUSE_REQUEST.unlink(missing_ok=True)
        if running:
            COLLECTOR_STOP_REQUEST.touch()
            if not wait_for_collector(False):
                raise OSError("The collector is busy and did not stop safely yet.")
        else:
            messages[action] = "The collector was already stopped."
    elif action == "open_folder":
        os.startfile(ARCHIVE_DIRECTORY)

    return {"ok": True, "message": messages[action], "collector": collector_status()}


def collector_status() -> dict[str, Any]:
    log_exists = COLLECTOR_LOG.is_file()
    last_update: str | None = None
    seconds_since_update: int | None = None
    latest_log = ""

    if log_exists:
        modified = datetime.fromtimestamp(
            COLLECTOR_LOG.stat().st_mtime, timezone.utc
        ).astimezone(SRI_LANKA_TIMEZONE)
        last_update = modified.isoformat()
        seconds_since_update = max(
            0, round((datetime.now(timezone.utc) - modified.astimezone(timezone.utc)).total_seconds())
        )
        with COLLECTOR_LOG.open("r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                if line.strip():
                    latest_log = line.strip()

    running = collector_mutex_is_open()
    paused = running and COLLECTOR_PAUSE_REQUEST.is_file()
    recent = seconds_since_update is not None and seconds_since_update <= 90
    if paused:
        label = "PAUSED"
        confidence = "Paused"
    elif running and recent:
        label = "RUNNING"
        confidence = "High"
    elif running:
        # An idle collector deliberately does not log every empty 20-second
        # scan. A live mutex therefore means it is healthy and waiting for a
        # new visit, not that it has stopped responding.
        label = "RUNNING - idle"
        confidence = "High"
    elif recent:
        label = "RECENT UPDATE"
        confidence = "Medium"
    else:
        label = "NEEDS ATTENTION"
        confidence = "Low"

    return {
        "running": running,
        "recent": recent,
        "healthy": running and not paused,
        "paused": paused,
        "label": label,
        "last_update": last_update,
        "seconds_since_update": seconds_since_update,
        "latest_log": latest_log,
        "archive_available": ARCHIVE_DATABASE.is_file(),
        "automatic_start_installed": STARTUP_LAUNCHER.is_file(),
        "confidence": confidence,
        "process_ids": collector_process_ids() if running else [],
        "check_interval_seconds": 20,
        "next_scan_seconds": max(0, 20 - (seconds_since_update or 0)) if running and not paused else None,
        "log_lines": recent_collector_log(),
        "archive_size_mb": round(ARCHIVE_DATABASE.stat().st_size / (1024 * 1024), 2)
        if ARCHIVE_DATABASE.is_file()
        else 0,
    }


def trend_data(connection: sqlite3.Connection, where: str, parameters: list[str], filters: dict[str, Any]) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT visited_at_utc FROM permanent_visits" + where,
        parameters,
    ).fetchall()
    counts = Counter(sri_lanka_date(row["visited_at_utc"]) for row in rows if row["visited_at_utc"])
    end_day = filters["end"] or datetime.now(SRI_LANKA_TIMEZONE).date()
    if filters["start"]:
        start_day = filters["start"]
    else:
        start_day = end_day - timedelta(days=13)
    if (end_day - start_day).days > 29:
        start_day = end_day - timedelta(days=29)
    days = []
    current = start_day
    while current <= end_day:
        days.append(
            {
                "label": current.isoformat(),
                "short_label": current.strftime("%d %b"),
                "count": counts[current],
            }
        )
        current += timedelta(days=1)
    return days


def range_label(filters: dict[str, Any]) -> str:
    def format_bound(day: date, at_time: clock_time | None) -> str:
        value = day.strftime("%d %b %Y")
        return f"{value} {at_time.strftime('%H:%M')}" if at_time else value

    if filters["start"] and filters["end"]:
        label = (
            f"{format_bound(filters['start'], filters.get('start_time'))} – "
            f"{format_bound(filters['end'], filters.get('end_time'))}"
        )
    elif filters["start"]:
        label = f"Since {format_bound(filters['start'], filters.get('start_time'))}"
    elif filters["end"]:
        label = f"Until {format_bound(filters['end'], filters.get('end_time'))}"
    else:
        label = "All archived history"
    if filters.get("hour") is not None:
        label += f" · {filters['hour']:02d}:00 – {filters['hour']:02d}:59"
    return label


def dashboard_data(filters: dict[str, Any]) -> dict[str, Any]:
    where, parameters = build_where(filters)
    with open_archive(filters) as connection:
        summary = connection.execute(
            """
            SELECT
                COUNT(*) AS visits,
                COUNT(DISTINCT NULLIF(domain, '')) AS domains,
                MIN(visited_at_utc) AS first_visit,
                MAX(visited_at_utc) AS last_visit,
                COALESCE(SUM(observation_count-1),0) AS grouped_observations,
                COALESCE(SUM(duplicate_status='possible'),0) AS flagged_observations
            FROM permanent_visits
            """
            + where,
            parameters,
        ).fetchone()

        domain_where = where + " AND domain <> ''" if where else " WHERE domain <> ''"
        domains = connection.execute(
            """
            SELECT domain, COUNT(*) AS count
            FROM permanent_visits
            """
            + domain_where
            + """
            GROUP BY domain
            ORDER BY count DESC, domain ASC
            LIMIT 10
            """,
            parameters,
        ).fetchall()

        total = int(summary["visits"])
        pages = max(1, (total + filters["limit"] - 1) // filters["limit"])
        page = min(filters["page"], pages)
        offset = (page - 1) * filters["limit"]
        visits = connection.execute(
            """
            SELECT archive_id, visited_at_utc, title, url, domain, capture_source, duplicate_status, observation_count, observed_url, url_quality, title_source, tab_mode
            FROM permanent_visits
            """
            + where
            + """
            ORDER BY chrome_visit_time DESC, archive_id DESC
            LIMIT ? OFFSET ?
            """,
            [*parameters, filters["limit"], offset],
        ).fetchall()

        visit_items = []
        for row in visits:
            item = dict(row)
            ids = [row['archive_id']]
            if row['duplicate_status'] == 'grouped':
                ids += [-r[0] for r in connection.execute('SELECT mobile_id FROM duplicate_candidates WHERE desktop_id=? AND groupable=1 ORDER BY mobile_id', (row['archive_id'],))]
            item['delete_refs'] = []
            for visit_id in ids:
                if visit_id > 0:
                    original = connection.execute('SELECT url,chrome_visit_time,transition FROM main.permanent_visits WHERE archive_id=?', (visit_id,)).fetchone()
                    ref = [original[0], original[1], original[2]]
                else:
                    original = connection.execute('SELECT kind,id FROM shared.remote WHERE rowid=?', (-visit_id,)).fetchone()
                    ref = [original[0], original[1]]
                item['delete_refs'].append({'id': visit_id, 'digest': deletion_digest(ref)})
            visit_items.append(item)
        trend = trend_data(connection, where, parameters, filters)
        searches = search_analytics(connection, where, parameters)
        nsfw = nsfw_analytics(connection, where, parameters)
        patterns = activity_patterns(connection, where, parameters, filters)
        insights = advanced_insights(connection, where, parameters)

    collector = collector_status()

    return {
        "summary": {
            "visits": total,
            "domains": int(summary["domains"]),
            "first_visit": summary["first_visit"],
            "last_visit": summary["last_visit"],
            "range_label": range_label(filters),
            "grouped_observations": int(summary['grouped_observations']),
            "flagged_observations": int(summary['flagged_observations']),
        },
        "trend": trend,
        "searches": searches,
        "nsfw": nsfw,
        "patterns": patterns,
        "insights": insights,
        "collector_status": collector,
        "top_domains": [{"domain": row["domain"], "count": row["count"]} for row in domains],
        "visits": {
            "items": visit_items,
            "total": total,
            "page": page,
            "pages": pages,
        },
    }


def session_activity(query: dict[str, list[str]]) -> dict[str, Any]:
    raw_start = first_value(query, "start")
    raw_end = first_value(query, "end")
    if not raw_start or not raw_end:
        raise ValueError("A session start and end are required.")
    start = datetime.fromisoformat(raw_start)
    end = datetime.fromisoformat(raw_end)
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError("The session time range is invalid.")
    start_utc = start.astimezone(timezone.utc).isoformat()
    end_utc = end.astimezone(timezone.utc).isoformat()
    source = first_value(query,'source')
    source_where = " AND capture_source IN ('chrome_android','chrome_android_share','chrome_profile13+chrome_android')" if source == 'android' else " AND capture_source IN ('chrome_profile13','chrome_profile13+chrome_android')" if source == 'desktop' else ''
    with open_archive({'source': source, 'duplicates':first_value(query,'duplicates')}) as connection:
        rows = connection.execute(
            """
            SELECT visited_at_utc, title, url, domain, visit_duration
            FROM permanent_visits
            WHERE visited_at_utc >= ? AND visited_at_utc <= ?
            """ + source_where + " ORDER BY chrome_visit_time ASC, archive_id ASC",
            [start_utc, end_utc],
        ).fetchall()
    domains = Counter(row["domain"] for row in rows if row["domain"])
    return {
        "start": start_utc,
        "end": end_utc,
        "visits": len(rows),
        "top_domain": domains.most_common(1)[0][0] if domains else "—",
        "items": [dict(row) for row in rows],
    }


def csv_export(filters: dict[str, Any]) -> str:
    where, parameters = build_where(filters)
    with open_archive(filters) as connection:
        rows = connection.execute(
            """
            SELECT visited_at_utc, title, url, domain, first_archived_at_utc, capture_source, duplicate_status, observation_count, observed_url, url_quality, title_source, tab_mode
            FROM permanent_visits
            """
            + where
            + """
            ORDER BY chrome_visit_time DESC, archive_id DESC
            """,
            parameters,
        ).fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "Visited at Sri Lanka time (UTC+05:30)",
            "Title",
            "URL",
            "Domain",
            "First archived at Sri Lanka time (UTC+05:30)",
            "Capture source",
            "Duplicate status",
            "Observation count",
            "Observed URL",
            "URL source",
            "Title source",
            "Tab mode",
        ]
    )
    writer.writerows(
        [
            [
                sri_lanka_iso(row["visited_at_utc"]),
                row["title"],
                row["url"],
                row["domain"],
                sri_lanka_iso(row["first_archived_at_utc"]),
                row["capture_source"],
                row['duplicate_status'],
                row['observation_count'],
                row['observed_url'],row['url_quality'],row['title_source'],row['tab_mode'],
            ]
            for row in rows
        ]
    )
    return output.getvalue()


def nsfw_csv_export(filters: dict[str, Any]) -> str:
    where, parameters = build_where(filters)
    with open_archive(filters) as connection:
        report = nsfw_analytics(connection, where, parameters)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Visited at Sri Lanka time (UTC+05:30)", "Confidence", "Match reason", "Type", "Website", "Search query", "Title", "URL"])
    for event in report["events_detail"]:
        writer.writerow([sri_lanka_iso(event["visited_at_utc"]), event["confidence"], event["reason"], event["type"], event["domain"], event["query"], event["title"], event["url"]])
    return output.getvalue()


def preview_key(raw_url: str) -> str:
    return hashlib.sha256(raw_url.encode("utf-8")).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    """Metadata previews never follow redirects to an unchecked destination."""

    def redirect_request(self, request: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def public_http_url(raw_url: str) -> bool:
    """Avoid using a history entry to make the local server request private hosts."""

    try:
        parsed = urlparse(raw_url)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host or host.casefold() == "localhost":
            return False
        for result in socket.getaddrinfo(host, None):
            address = ipaddress.ip_address(result[4][0])
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast:
                return False
        return True
    except (OSError, ValueError):
        return False


def youtube_thumbnail(raw_url: str) -> str | None:
    try:
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").casefold()
        if host == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0]
        elif "youtube.com" in host:
            video_id = parse_qs(parsed.query).get("v", [""])[0] or (parsed.path.split("/")[2] if parsed.path.startswith(("/shorts/", "/embed/")) and len(parsed.path.split("/")) > 2 else "")
        else:
            return None
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id) else None
    except ValueError:
        return None


def fetch_public_bytes(raw_url: str, headers: dict[str, str], limit: int) -> tuple[str, bytes, str] | None:
    """Fetch a public page/image with redirect destinations checked one by one."""

    current = raw_url
    opener = build_opener(NoRedirect())
    for _ in range(5):
        if not public_http_url(current):
            return None
        try:
            with opener.open(Request(current, headers=headers), timeout=8) as response:
                final_url = response.geturl()
                if not public_http_url(final_url):
                    return None
                return response.headers.get_content_type(), response.read(limit), final_url
        except HTTPError as error:
            location = error.headers.get("Location") if error.headers else None
            if error.code not in {301, 302, 303, 307, 308} or not location:
                return None
            current = urljoin(current, location)
        except OSError:
            return None
    return None


def meta_thumbnail(raw_url: str) -> str | None:
    """Find a standard sharing image, with a small bounded fetch only on demand."""

    if not public_http_url(raw_url):
        return None
    fetched = fetch_public_bytes(raw_url, {"User-Agent": "Mozilla/5.0 (compatible; PrivateHistoryPreview/1.0)", "Accept": "text/html,image/*;q=0.8,*/*;q=0.1"}, 512_000)
    if not fetched:
        return None
    content_type, body, final_url = fetched
    if not public_http_url(final_url):
        return None
    if content_type.startswith("image/"):
        return final_url
    if content_type not in {"text/html", "application/xhtml+xml"}:
        return None
    page = body.decode("utf-8", errors="ignore")
    for tag in re.findall(r"<meta\\b[^>]*>", page, flags=re.IGNORECASE):
        attributes = dict((name.casefold(), html.unescape(value)) for name, _, value in re.findall(r"([\\w:-]+)\\s*=\\s*(['\"])(.*?)\\2", tag, flags=re.IGNORECASE | re.DOTALL))
        marker = (attributes.get("property") or attributes.get("name") or "").casefold()
        candidate = attributes.get("content", "").strip()
        if marker in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"} and candidate:
            candidate = urljoin(final_url, candidate)
            if public_http_url(candidate):
                return candidate
    return None


def cached_thumbnail(raw_url: str) -> dict[str, str | None]:
    key = preview_key(raw_url)
    metadata = THUMBNAIL_CACHE / f"{key}.json"
    if metadata.is_file():
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
            if data.get("ready"):
                return {"image_url": f"/previews/{key}"}
            checked_at = data.get("checked_at")
            if checked_at and datetime.now(timezone.utc) - datetime.fromisoformat(checked_at) < timedelta(hours=6):
                return {"image_url": None}
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    THUMBNAIL_CACHE.mkdir(parents=True, exist_ok=True)
    failed_metadata = lambda: json.dumps({"ready": False, "checked_at": datetime.now(timezone.utc).isoformat()})
    candidate = youtube_thumbnail(raw_url) or meta_thumbnail(raw_url)
    if not candidate or not public_http_url(candidate):
        metadata.write_text(failed_metadata(), encoding="utf-8")
        return {"image_url": None}
    fetched = fetch_public_bytes(candidate, {"User-Agent": "Mozilla/5.0 (compatible; PrivateHistoryPreview/1.0)", "Accept": "image/*"}, 2_500_000)
    if not fetched:
        metadata.write_text(failed_metadata(), encoding="utf-8")
        return {"image_url": None}
    content_type, content, _ = fetched
    if not content_type.startswith("image/") or not content:
        metadata.write_text(failed_metadata(), encoding="utf-8")
        return {"image_url": None}
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(content_type, ".img")
    image_path = THUMBNAIL_CACHE / f"{key}{extension}"
    image_path.write_bytes(content)
    metadata.write_text(json.dumps({"ready": True, "file": image_path.name, "content_type": content_type, "checked_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    return {"image_url": f"/previews/{key}"}


def cached_thumbnail_file(key: str) -> tuple[Path, str] | None:
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        return None
    metadata = THUMBNAIL_CACHE / f"{key}.json"
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
        image_path = THUMBNAIL_CACHE / data["file"]
        if data.get("ready") and image_path.is_file() and image_path.parent == THUMBNAIL_CACHE:
            return image_path, data.get("content_type", "application/octet-stream")
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return None


def deletion_digest(value: list[Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def delete_visits(refs: Any) -> dict[str, Any]:
    if not isinstance(refs, list) or not 1 <= len(refs) <= 1000:
        raise ValueError('Select between 1 and 1000 observations.')
    if any(not isinstance(r, dict) or type(r.get('id')) is not int or r['id'] == 0 or not isinstance(r.get('digest'), str) or not re.fullmatch(r'[a-f0-9]{64}', r['digest']) for r in refs):
        raise ValueError('Invalid visit selection.')
    unique = {r['id']: r['digest'] for r in refs}
    if len(unique) != len(refs):
        raise ValueError('Duplicate visit selection. Refresh and try again.')
    cloud = ARCHIVE_DIRECTORY / 'SharedArchive.sqlite'
    with closing(sqlite3.connect(ARCHIVE_DATABASE, timeout=30)) as db:
        db.execute('PRAGMA secure_delete=ON')
        if cloud.is_file():
            db.execute('ATTACH DATABASE ? AS shared', (str(cloud),))
            db.execute('PRAGMA shared.secure_delete=ON')
        db.execute('BEGIN IMMEDIATE')
        desktop, remote = [], []
        for visit_id, expected in unique.items():
            if visit_id > 0:
                row = db.execute('SELECT url,chrome_visit_time,transition FROM main.permanent_visits WHERE archive_id=?', (visit_id,)).fetchone()
                if not row or deletion_digest(list(row)) != expected: raise ValueError('A selected visit changed. Refresh and try again.')
                desktop.append((visit_id, expected))
            else:
                if not cloud.is_file(): raise ValueError('Mobile archive unavailable. Refresh and try again.')
                row = db.execute('SELECT kind,id FROM shared.remote WHERE rowid=?', (-visit_id,)).fetchone()
                if not row or deletion_digest(list(row)) != expected: raise ValueError('A selected visit changed. Refresh and try again.')
                remote.append((-visit_id, expected))
        db.execute('CREATE TABLE IF NOT EXISTS deleted_visit_hashes(digest TEXT PRIMARY KEY)')
        db.executemany('INSERT OR IGNORE INTO deleted_visit_hashes VALUES (?)', [(r[1],) for r in desktop])
        db.execute('DROP TRIGGER IF EXISTS permanent_visits_no_delete')
        if db.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='manual_visit_provenance'").fetchone():
            db.executemany('DELETE FROM manual_visit_provenance WHERE archive_id=?', [(r[0],) for r in desktop])
        db.executemany('DELETE FROM main.permanent_visits WHERE archive_id=?', [(r[0],) for r in desktop])
        db.execute("CREATE TRIGGER permanent_visits_no_delete BEFORE DELETE ON permanent_visits BEGIN SELECT RAISE(ABORT, 'permanent_visits is append-only'); END")
        if remote:
            db.execute('CREATE TABLE IF NOT EXISTS shared.deleted_remote_hashes(digest TEXT PRIMARY KEY)')
            db.executemany('INSERT OR IGNORE INTO shared.deleted_remote_hashes VALUES (?)', [(r[1],) for r in remote])
            db.executemany('DELETE FROM shared.remote WHERE rowid=?', [(r[0],) for r in remote])
        db.commit()
    try:
        from history_collector import export_csv
        with closing(sqlite3.connect(ARCHIVE_DATABASE, timeout=30)) as db: export_csv(db)
    except (OSError, sqlite3.Error) as error:
        return {'message': f'Deleted {len(desktop)} desktop and {len(remote)} mobile records, but the managed CSV could not be refreshed: {error}. Refresh it before relying on deletion.'}
    return {'message': f'Deleted {len(desktop)} desktop and {len(remote)} mobile archive records. Other copies may remain.'}


def delete_search(query: str) -> dict[str, Any]:
    """Delete exact matching search observations from the two local archives."""
    if not isinstance(query, str) or not 2 <= len(query) <= 200:
        raise ValueError("Invalid search query.")
    wanted = query.casefold()
    cloud = ARCHIVE_DIRECTORY / 'SharedArchive.sqlite'
    with closing(sqlite3.connect(ARCHIVE_DATABASE, timeout=30)) as db:
        db.execute('PRAGMA secure_delete=ON')
        if cloud.is_file():
            db.execute('ATTACH DATABASE ? AS shared', (str(cloud),))
            db.execute('PRAGMA shared.secure_delete=ON')
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS deleted_visit_hashes(digest TEXT PRIMARY KEY)')
        rows = db.execute('SELECT archive_id,url,chrome_visit_time,transition FROM main.permanent_visits').fetchall()
        matches = [row for row in rows if (found := extract_search_query(row[1])) and found[0].casefold() == wanted]
        digest = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        db.executemany('INSERT OR IGNORE INTO deleted_visit_hashes VALUES (?)', [(digest([r[1], r[2], r[3]]),) for r in matches])
        db.execute('DROP TRIGGER IF EXISTS permanent_visits_no_delete')
        if db.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='manual_visit_provenance'").fetchone():
            db.executemany('DELETE FROM manual_visit_provenance WHERE archive_id=?', [(r[0],) for r in matches])
        db.executemany('DELETE FROM main.permanent_visits WHERE archive_id=?', [(r[0],) for r in matches])
        db.execute("CREATE TRIGGER permanent_visits_no_delete BEFORE DELETE ON permanent_visits BEGIN SELECT RAISE(ABORT, 'permanent_visits is append-only'); END")
        remote = []
        if cloud.is_file() and db.execute("SELECT 1 FROM shared.sqlite_master WHERE type='table' AND name='remote'").fetchone():
            db.execute('CREATE TABLE IF NOT EXISTS shared.deleted_remote_hashes(digest TEXT PRIMARY KEY)')
            remote = [r for r in db.execute('SELECT kind,id,url FROM shared.remote') if (found := extract_search_query(r[2])) and found[0].casefold() == wanted]
            db.executemany('INSERT OR IGNORE INTO shared.deleted_remote_hashes VALUES (?)', [(digest([r[0],r[1]]),) for r in remote])
            db.executemany('DELETE FROM shared.remote WHERE kind=? AND id=?', [(r[0],r[1]) for r in remote])
        db.commit()
    # Refresh the managed export after committing; an error must be reported to the user.
    from history_collector import export_csv
    with closing(sqlite3.connect(ARCHIVE_DATABASE, timeout=30)) as db:
        export_csv(db)
    return {'message': f'Deleted {len(matches)} desktop and {len(remote)} mobile archive records. Other copies may remain.'}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "PrivateHistoryDashboard/1.0"

    def local_request(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host','') == f'localhost:{self.server.server_port}':
            self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
            self.send_header('Location',f'http://127.0.0.1:{self.server.server_port}' + self.path)
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return False
        if self.headers.get('Host', '') not in allowed:
            self.send_json({'error':'Unexpected dashboard host'}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_GET(self) -> None:
        if not self.local_request(): return
        request = urlparse(self.path)
        if request.path == '/api/sync/status':
            self.send_json(shared_bridge().status())
            return
        if request.path == '/auth/callback':
            try:
                codes = parse_qs(request.query).get('code', [])
                if len(codes) != 1 or len(codes[0]) > 4096: raise SyncError('Missing authorization code; start sign-in again')
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                nonce = cookies['sync-login'].value if 'sync-login' in cookies else ''
                shared_bridge().finish_login(codes[0], nonce)
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header('Location', '/')
                self.send_header('Set-Cookie','sync-login=; Path=/auth/callback; HttpOnly; SameSite=Lax; Max-Age=0')
                self.send_header('Cache-Control','no-store')
                self.send_header('Referrer-Policy','no-referrer')
                self.end_headers()
            except (SyncError, ValueError, KeyError, CookieError):
                self.send_html('<h1>Sign-in could not complete</h1><p>Return to the dashboard and start sign-in again. Existing archive records are retained.</p><a href="/">Return to dashboard</a>')
            return
        if request.path == "/":
            self.send_html(DASHBOARD_HTML)
            return
        if request.path == "/api/dashboard":
            try:
                self.send_json(dashboard_data(filters_from_query(parse_qs(request.query))))
            except (OSError, sqlite3.Error, ValueError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if request.path == "/api/session":
            try:
                self.send_json(session_activity(parse_qs(request.query)))
            except (OSError, sqlite3.Error, ValueError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if request.path == "/api/export.csv":
            try:
                self.send_csv(csv_export(filters_from_query(parse_qs(request.query))))
            except (OSError, sqlite3.Error, ValueError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if request.path == "/api/nsfw.csv":
            try:
                self.send_csv(nsfw_csv_export(filters_from_query(parse_qs(request.query))), "NSFWIntelligenceReport.csv")
            except (OSError, sqlite3.Error, ValueError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if request.path == "/api/nsfw-rules":
            self.send_json(load_nsfw_rules())
            return
        if request.path == "/api/thumbnail":
            try:
                raw_url = first_value(parse_qs(request.query), "url")[:4000]
                self.send_json(cached_thumbnail(raw_url))
            except (OSError, ValueError) as error:
                self.send_json({"image_url": None, "error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if request.path.startswith("/previews/"):
            cached = cached_thumbnail_file(request.path.removeprefix("/previews/"))
            if cached:
                self.send_file(*cached)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Preview not found")
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Page not found")

    def do_POST(self) -> None:
        if not self.local_request(): return
        request = urlparse(self.path)
        if request.path in ('/api/delete-search', '/api/delete-visits'):
            try:
                allowed = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
                if self.headers.get('Origin', '') not in allowed or self.headers.get('X-DataSync') != '1':
                    self.send_json({'error': 'Cross-site deletion blocked'}, HTTPStatus.FORBIDDEN); return
                if not self.headers.get('Content-Type', '').lower().startswith('application/json'): raise ValueError('JSON required')
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 150000: raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(length))
                self.send_json(delete_search(payload.get('query')) if request.path == '/api/delete-search' else delete_visits(payload.get('visits')))
            except (OSError, sqlite3.Error, ValueError, TypeError) as error:
                self.send_json({'error': str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if request.path.startswith('/api/sync/'):
            try:
                allowed = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
                if self.headers.get('Origin','') not in allowed or self.headers.get('X-DataSync') != '1':
                    self.send_json({'error':'Cross-site sync controls are blocked'}, HTTPStatus.FORBIDDEN)
                    return
                if not self.headers.get('Content-Type','').lower().startswith('application/json'): raise ValueError('JSON required')
                length = int(self.headers.get('Content-Length','0'))
                if not 1 <= length <= 16000: raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload,dict): raise ValueError('Expected a JSON object')
                bridge = shared_bridge()
                if request.path == '/api/sync/configure':
                    bridge.configure(str(payload.get('url','')),str(payload.get('key','')),payload.get('include_visits') is True)
                elif request.path == '/api/sync/login':
                    url, nonce = bridge.login()
                    content = json.dumps({'url':url}).encode()
                    self.send_response(HTTPStatus.OK)
                    self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(content)))
                    self.send_header('Cache-Control','no-store')
                    self.send_header('Set-Cookie',f'sync-login={nonce}; Path=/auth/callback; HttpOnly; SameSite=Lax; Max-Age=600')
                    self.end_headers(); self.wfile.write(content)
                    return
                elif request.path == '/api/sync/signout': bridge.signout()
                elif request.path == '/api/sync/now': bridge.wake()
                else: raise ValueError('Unknown sync action')
                self.send_json(bridge.status())
            except (SyncError, OSError, ValueError, sqlite3.Error):
                self.send_json({'error':'Sync action failed. Check configuration, original account, shared schema and network. Records retained.'}, HTTPStatus.BAD_REQUEST)
            return
        if request.path == "/api/collector/control":
            try:
                origin = self.headers.get("Origin", "")
                if origin and origin not in {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}:
                    self.send_json({"error": "Cross-site collector controls are blocked."}, HTTPStatus.FORBIDDEN)
                    return
                if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                    self.send_json({"error": "Collector controls require JSON."}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 1024:
                    raise ValueError("Invalid collector control request size.")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_json(collector_control(str(payload.get("action", ""))))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if request.path != "/api/nsfw-rules":
            self.send_error(HTTPStatus.NOT_FOUND, "Page not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 50000:
                raise ValueError("Invalid rule request size.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self.send_json(save_nsfw_rules(payload))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def send_html(self, body: str) -> None:
        content = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def send_csv(self, text: str, filename: str = "FilteredChromeHistory.csv") -> None:
        content = text.encode("utf-8-sig")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename={filename}",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def send_file(self, path: Path, content_type: str) -> None:
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=2592000")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    if not ARCHIVE_DATABASE.is_file():
        print(f"Archive database not found: {ARCHIVE_DATABASE}")
        return 1
    try:
        server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    except OSError as error:
        print(f"Could not start the dashboard at http://{HOST}:{PORT}: {error}")
        return 1
    print(f"Private dashboard running at http://{HOST}:{PORT}")
    shared_bridge().start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shared_bridge().stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
