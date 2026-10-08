"""
Checks for the presenter briefing assistant embedded in the single-file
frontend (2026-10). It runs the real module text, extracted from the HTML
between its BRIEFING:START/END markers, under Node with a stub browser.

What this does NOT cover: actual speech output and the on-screen panel need a
real browser with speech voices; nothing here can hear or see them.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[2] / "frontend" / "defence-opportunity-intelligence-app-v12.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
const src = html.slice(html.indexOf('/* BRIEFING:START'), html.indexOf('/* BRIEFING:END */'));
global.window = global;
let touchedDocument = false;
global.document = new Proxy({}, {get(){ touchedDocument = true; throw new Error('document used'); }});
global.VENDOR_NAME_RE = /\bsyas\b/i;
global.currentUser = null;
(0, eval)(src);
const A = window.BriefingAssistant;
const out = {};

// every page the router can render has a briefing
const routerMap = html.match(/const fns = \{([\s\S]*?)\};/)[1];
const modes = [...routerMap.matchAll(/(?:'([a-z-]+)'|\b([a-z]+)):\s*render/g)].map(m => m[1] || m[2]);
out.modes = modes;
out.missing = modes.filter(m => !A.BRIEFINGS[m]);

// voice choice never lands on a male voice
const v = (name, lang) => ({name, lang, voiceURI: name});
const voices = [
  v('Microsoft David Desktop - English (United States)', 'en-US'), v('Microsoft Zira Desktop - English (United States)', 'en-US'),
  v('Microsoft Hemant - Hindi (India)', 'hi-IN'), v('Microsoft Heera - English (India)', 'en-IN'),
  v('Google UK English Female', 'en-GB'), v('Google UK English Male', 'en-GB'),
  v('Microsoft Neerja Online (Natural) - English (India)', 'en-IN'), v('Alex', 'en-US'), v('Samantha', 'en-US'),
  v('Microsoft Mark - English (United States)', 'en-US'), v('Google हिन्दी', 'hi-IN'),
];
const fem = A.femaleVoices(voices).map(x => x.name);
out.female = fem;
out.onlyMale = A.femaleVoices([v('Microsoft David', 'en-US'), v('Alex', 'en-US'), v('Google UK English Male', 'en-GB')]).length;

// pronunciation + script shape
out.say = A.speakable('OEM partners for a UAV, see SAM.gov and a PDF');
const s = A.buildScript(A.BRIEFINGS.dashboard);
out.kinds = s.map(i => i.kind);

// presenter gating: only a Syas account may touch the DOM at all
currentUser = {company_name: 'M/s Alpha_Elsec defence and Aerospace Systems Pvt Ltd'};
window.briefingOnPage('home'); out.customerTouchedDoc = touchedDocument;
currentUser = null;
window.briefingOnPage('home'); out.anonTouchedDoc = touchedDocument;
currentUser = {company_name: 'Syas AI & Automation'};
try { window.briefingOnPage('home'); } catch (e) { out.syasError = e.message; }
out.syasTouchedDoc = touchedDocument;

out.allText = JSON.stringify(A.BRIEFINGS);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result():
    proc = subprocess.run(["node", "-e", HARNESS, str(HTML)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_every_routable_page_has_a_briefing(result):
    assert len(result["modes"]) >= 20, result["modes"]
    assert result["missing"] == [], f"pages without a briefing: {result['missing']}"


def test_voice_is_always_female_and_never_a_male_voice(result):
    female = result["female"]
    assert female, "expected at least one female English voice from the mock list"
    for name in female:
        assert not re.search(r"\b(david|mark|alex|hemant)\b|male$", name, re.I) or "female" in name.lower(), name
    assert "Microsoft David Desktop - English (United States)" not in female
    assert "Google UK English Male" not in female and "Alex" not in female
    assert all("hindi" not in n.lower() and "ह" not in n for n in female), "English script needs an English voice"
    assert result["female"][0].startswith("Microsoft Neerja"), "en-IN natural voice should rank first"
    assert result["onlyMale"] == 0, "with only male voices nothing is chosen silently"


def test_acronyms_are_spoken_letter_by_letter(result):
    assert result["say"] == "O E M partners for a U A V, see Sam dot gov and a P D F"


def test_script_is_intro_points_then_positives(result):
    kinds = result["kinds"]
    assert kinds[0] == "intro" and "head" in kinds and kinds.count("positive") >= 2


def test_only_syas_accounts_get_the_assistant(result):
    assert result["customerTouchedDoc"] is False, "a customer tenant must never get the assistant"
    assert result["anonTouchedDoc"] is False
    assert result["syasTouchedDoc"] is True and result["syasError"] == "document used"


def test_script_text_has_no_technical_internals_and_no_superlatives(result):
    text = result["allText"].lower()
    banned = ["postgres", "fastapi", "sqlalchemy", "jwt", "fernet", "docker", "caddy", "row-level", "row level",
              "api key", "secret", "encryption key", "alembic", "asyncpg", "uvicorn", "migration", ".env",
              "best in the world", "world's best", "world-class", "number one", "unbeatable", "guarantee"]
    assert [w for w in banned if w in text] == []
