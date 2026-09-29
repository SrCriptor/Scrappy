"""
Donor avatar catalog.

Physically isolates anonymous donor profile images from the admin/staff
avatar library (static/avatars/). Donor avatars live in static/donors/
and are discovered dynamically by filename convention:

    donor_m_<id>.png  ->  Masculino
    donor_f_<id>.png  ->  Feminino

This means adding/removing avatar files in static/donors/ is enough —
no code change is required. The catalog is (re)scanned from disk on every
call, so newly-dropped files are picked up immediately (a restart is not
required, though app.py also re-verifies the directory at startup).

This module exposes the canonical id lists/paths so the backend can
validate `avatar_id` and the frontend gender toggle can be generated
from a single source of truth: the files actually present on disk.
"""

import os
import re
import logging

DONOR_DIR = os.path.join('static', 'donors')

_FILENAME_RE = re.compile(r'^donor_(m|f)_([A-Za-z0-9]+)\.png$', re.IGNORECASE)

DEFAULT_AVATAR_ID = 'm_1'


def _sort_key(avatar_id: str):
    """Natural sort: '1' < '1a' < '1b' < '2' < ... < '10' < '12'."""
    m = re.match(r'^(\d+)([A-Za-z]*)$', avatar_id)
    if not m:
        return (float('inf'), avatar_id)
    return (int(m.group(1)), m.group(2))


def scan_avatar_ids():
    """
    Scan static/donors/ for donor_m_*.png / donor_f_*.png files and return
    (male_ids, female_ids) — each a naturally-sorted list of bare ids
    (e.g. 'm_1', 'f_3a') without the 'donor_' prefix or '.png' suffix.
    """
    male, female = [], []
    try:
        entries = os.listdir(DONOR_DIR)
    except FileNotFoundError:
        entries = []

    for filename in entries:
        match = _FILENAME_RE.match(filename)
        if not match:
            continue
        gender, raw_id = match.group(1).lower(), match.group(2)
        avatar_id = f'{gender}_{raw_id}'
        (male if gender == 'm' else female).append(avatar_id)

    male.sort(key=lambda aid: _sort_key(aid[2:]))
    female.sort(key=lambda aid: _sort_key(aid[2:]))
    return male, female


def get_male_avatar_ids():
    return scan_avatar_ids()[0]


def get_female_avatar_ids():
    return scan_avatar_ids()[1]


def get_valid_avatar_ids():
    male, female = scan_avatar_ids()
    return frozenset(male + female)


def get_default_avatar_id():
    """First discovered male avatar, falling back to DEFAULT_AVATAR_ID, then
    to whatever avatar (any gender) exists, so a totally empty/renamed
    directory never crashes avatar_path()."""
    male, female = scan_avatar_ids()
    if DEFAULT_AVATAR_ID in male:
        return DEFAULT_AVATAR_ID
    if male:
        return male[0]
    if female:
        return female[0]
    return DEFAULT_AVATAR_ID


def avatar_path(avatar_id: str) -> str:
    """Return the static path for a given avatar id, falling back to a
    valid default when the id is unknown/missing."""
    valid_ids = get_valid_avatar_ids()
    aid = avatar_id if avatar_id in valid_ids else get_default_avatar_id()
    return os.path.join(DONOR_DIR, f'donor_{aid}.png')


def ensure_donor_avatars():
    """
    Verify static/donors/ exists and log the current dynamically-discovered
    catalog. Files are curated hand-picked assets uploaded by the user —
    this only guards against a missing/empty directory, it never generates
    procedural art.
    """
    os.makedirs(DONOR_DIR, exist_ok=True)

    male, female = scan_avatar_ids()
    logger = logging.getLogger(__name__)

    _R    = '\033[0m'
    _BLUE = '\033[1;34m'
    _PINK = '\033[1;35m'
    _CYAN = '\033[1;36m'
    _DIM  = '\033[2m'

    if not male and not female:
        logger.warning('donor_avatars: no donor_m_*/donor_f_* files found in %s', DONOR_DIR)
    else:
        print(
            f"  {_DIM}┌─{_R} {_CYAN}donor_avatars{_R}  "
            f"{_BLUE}♂ {len(male)} masculino{_R}  {_DIM}·{_R}  "
            f"{_PINK}♀ {len(female)} feminino{_R}  "
            f"{_DIM}└─ {DONOR_DIR}{_R}"
        )
