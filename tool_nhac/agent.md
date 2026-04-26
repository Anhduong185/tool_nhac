# TikTok Audio Automation System Architecture

## Overview

This system automates the discovery, filtering, and validation of TikTok audio clips based on multiple keywords.

The goal is to reduce manual searching time from **2–3 hours/day** to **30–60 minutes/day**, while maintaining strict filtering quality.

Target output:

≥100 valid audio clips/day

---

# System Goals

Primary goals:

* Automate TikTok keyword search
* Extract audio metadata
* Apply strict rule filtering
* Detect duplicate audio
* Validate non-music speech content
* Export usable results

Secondary goals:

* Detect trending audio growth
* Integrate AI speech detection
* Enable scalable processing
* Maintain production stability

---

# High-Level Architecture

```
keywords.txt
        ↓
Keyword Loader
        ↓
TikTok Crawler (Playwright)
        ↓
Metadata Extractor
        ↓
Rule-Based Filter
        ↓
Duplicate Checker (SQLite)
        ↓
Audio Downloader
        ↓
Shazam Validation
        ↓
Speech Detection (AI)
        ↓
Export Results (CSV)
```

---

# Module Architecture

## 1. config.py

Central configuration file.

Contains:

* scroll_count
* min_usage
* max_duration
* blacklist_keywords
* file paths
* timeout settings

Example:

```python
MAX_DURATION = 59
MIN_USAGE = 500

SCROLL_COUNT = 300

BLACKLIST = [
    "official",
    "remix",
    "movie",
    "soundtrack",
    "promo"
]
```

---

## 2. main.py

Entry point of the system.

Responsibilities:

* Load keywords
* Run crawler
* Execute filtering pipeline
* Manage workflow
* Handle errors

Main workflow:

```
load keywords
for keyword in keywords:
    crawl videos
    extract audio
    filter audio
    save results
```

---

## 3. crawler.py

Core TikTok crawling engine.

Uses:

Playwright (Python)

Responsibilities:

* Open TikTok search
* Search keyword
* Scroll dynamically
* Extract metadata

Key operations:

* dynamic scrolling
* DOM parsing
* metadata extraction

Important:

Scrolling depth determines dataset size.

Recommended:

200–500 scroll cycles

---

## 4. filter.py

Rule-based filtering engine.

Responsibilities:

* Apply duration rules
* Apply usage rules
* Apply blacklist rules

Core function:

```
def is_valid_audio(audio):
```

Filter logic:

Accept:

duration ≤ 59 seconds

AND

usage ≥ 500

Reject:

brand-related names
song-related names

---

## 5. database.py

Duplicate prevention module.

Database:

SQLite

Table:

audio_history

Fields:

audio_id
audio_name
date_added

Responsibilities:

* Check duplicate audio
* Insert new records
* Maintain history

---

## 6. audio_processor.py

Handles audio downloading and validation.

Responsibilities:

* Download audio preview
* Run Shazam detection
* Classify audio type

Libraries:

* requests
* shazamio

Future:

* speech classifier
* audio quality scoring

---

## 7. exporter.py

Handles result export.

Supported formats:

* CSV
* JSON
* Google Sheets (future)

CSV Fields:

audio_id
audio_name
duration
usage_count
audio_url
status

---

# Data Flow

```
keywords.txt
        ↓
crawler.py
        ↓
filter.py
        ↓
database.py
        ↓
audio_processor.py
        ↓
exporter.py
```

---

# Keyword System

Keywords are stored in:

```
keywords.txt
```

Supports:

* Vietnamese
* English
* Mixed language

Example:

```
review mỹ phẩm
bán hàng online
motivational speech
kinh doanh
life advice
```

Future Extension:

Keyword expansion engine.

Example:

```
kinh doanh
→ business tips
→ startup advice
→ entrepreneur speech
```

---

# Filtering Rules

Primary Rules:

```
duration ≤ 59 seconds
usage ≥ 500
```

Blacklist Rules:

Reject if contains:

* brand names
* music indicators
* promotional keywords

Example:

```
official
remix
movie
promo
soundtrack
```

---

# Duplicate Prevention Strategy

Database:

SQLite

Purpose:

Avoid reusing audio already processed.

Logic:

```
if audio_id exists:

    skip
```

---

# Shazam Validation

Tool:

shazamio

Purpose:

Reject known songs.

Logic:

```
if shazam match found:

    reject audio
```

---

# Speech Detection (Future AI Phase)

Recommended Model:

YAMNet

Purpose:

Detect:

* speech
* music

Accept:

speech-only audio.

Reject:

music-heavy audio.

---

# Trending Detection (Future)

Goal:

Identify fast-growing audio.

Method:

Store usage_count per day.

Detect:

```
growth = today - yesterday
```

Accept:

if growth > threshold.

---

# Performance Optimization

Recommended techniques:

* asyncio
* batching
* caching
* parallel downloads

Reason:

Target processing:

≥100 audio/day

---

# Folder Structure

```
project_root/

│
├── main.py
├── config.py
├── crawler.py
├── filter.py
├── database.py
├── audio_processor.py
├── exporter.py
│
├── keywords.txt
│
├── audios/
│
├── database/
│   └── audio.db
│
├── logs/
│
└── output/
    └── results.csv
```

---

# Phase Development Roadmap

## Phase 1 — MVP

Includes:

* Keyword loading
* TikTok crawling
* Rule filtering
* Duplicate checking
* CSV export

Expected time:

3–5 days

---

## Phase 2 — Audio Intelligence

Includes:

* Audio download
* Shazam detection
* Speech classification

Expected time:

5–7 days

---

## Phase 3 — Full Automation

Includes:

* Keyword expansion
* Growth tracking
* Cloud storage
* Automation scheduling

Expected time:

7–10 days

---

# Future Extensions

Potential upgrades:

* Web dashboard
* Cloud execution
* API service
* Multi-account scaling
* AI recommendation engine

---

# Final Goal

Reduce manual work from:

2–3 hours/day

To:

30–60 minutes/day

While increasing audio discovery quality and consistency.
