# Handoff — where this stands and what to do next

Written for a fresh session (human or Claude) with no prior context. Read this,
then `README.md` for the architecture.

**Branch:** `claude/new-session-miyh25` (not `main`)
**Last commit:** `c5262cf` — "Rework calibration against real session frames"
**Status:** 188 tests passing, frontend builds clean, phases 1 and 5 working end
to end, phases 2–4 written but never run against real media.

---

## The one-line summary

Everything that can be verified without a video, an ffmpeg binary, or an API key
**is** verified. Everything that needs those three has never executed. The next
action is to run the pipeline against a real session file and report where it
stops.

---

## Next action

```bash
brew install ffmpeg
.venv/bin/pip install -e '.[media,llm]'
export ANTHROPIC_API_KEY=sk-ant-...

mkdir -p ~/.golf-analyzer/inbox
cp ~/Downloads/IMG_5852.MOV ~/.golf-analyzer/inbox/
.venv/bin/python -m backend.app.pipeline.watcher --once
```

It prints one line per stage. **Stage 2 (calibration) is the expected failure
point.** If it reports `needs_calibration`, that is designed behaviour, not a
crash — spec §4 says never guess a crop. Capture the output either way.

Before anything else, confirm there is an audio track at all:

```bash
ffprobe ~/Downloads/IMG_5852.MOV 2>&1 | grep Audio
```

If that is empty, phase 2 is dead on arrival and the plan changes.

---

## Four open questions

These block real progress and only a session with the video can answer them.

1. **Is the green badge in the panel header a running clock?** If it ticks, it
   is why the header is excluded from the change-detection diff. If it is
   static, that exclusion is harmless but unnecessary. Compare two frames a few
   seconds apart.
2. **Does the audio have usable narration?** Impact-onset detection, Whisper,
   and voice attribution are all untested against a real waveform. Strike
   location — the highest-value field in the system — depends entirely on this.
3. **Does stage 2 find the screen?** Calibration is screen-first (bright
   quadrilateral in a dark bay) with panel bounds derived as a fraction of it.
   Tuned to measurements from two stills; never run on video.
4. **Does the Full Swing app export CSV?** Check the hamburger menu in the stat
   panel. Spec §11: if session export exists, §2, §3's OCR portions, and the
   whole of phase 3 become unnecessary — stats would join to the audio track by
   timestamp instead. **This only ever removes work. Check it before investing
   further in vision.**

---

## What real frames already changed

Two frames from an actual bay were examined. Both spec §3 identities held
(`122.9/93.1 = 1.32`; `4.6 − 1.6 = 3.0`), confirming the §2 grid layout on three
independent shots, and apex tracked launch angle across all three, confirming
apex is in feet.

They also exposed three problems, all fixed in `c5262cf`:

- **Panel aspect is ~3.7 (h/w), detector accepted 1.29–3.43.** It would have
  rejected the correct panel every time. Window widened to 2.6–4.8.
- **Canonical canvas was 420×900 (2.1:1) for a 3.7:1 panel** — every digit
  stretched ~2× horizontally before OCR. Now 400×1400.
- **Panel header carries a shot counter and an apparent clock.** Stage 3 diffed
  the whole panel, so a ticking clock would report a shot roughly twice a
  second, forever. Header now excluded from the diff region and the 2×6 grid.

Also observed: row 6 is *legible* in both frames, so the CLAHE path may be
sufficient and the NULL-and-review fallback may rarely fire; and spin read 2193
and 2234 rpm with axes 1° and 10°, so spec §12.2's "spin reads zero on this
unit" assumption looks stale.

---

## Verified vs unverified

| Area | State |
|---|---|
| Schema, validation, store | Verified — 52 tests |
| Analysis layer (all five §7 views) | Verified — 33 tests |
| HTTP API | Verified — 29 tests |
| Review UI, all analysis screens | Verified — rendered against seeded data, light and dark |
| Insight agent + SQL guard | Verified — 33 tests, model stubbed |
| Shot binding logic | Verified — 11 tests, pure function |
| Club persistence / change guard | Verified — 13 tests, pure function |
| Panel geometry | Verified against *measurements*, not pixels — 15 tests |
| Frame grabbing, screen detection | **Never run** |
| Whisper transcription | **Never run** |
| ffmpeg ingest, audio extraction, clips | **Never run** |
| Live vision / voice / insight model calls | **Never run** |

---

## Missing fixtures

The frames used for calibration measurements arrived as chat images and could
not be written to disk, so `test_calibration_geometry.py` encodes hand-measured
numbers rather than real pixels. **Committing 5–6 session frames to
`backend/tests/fixtures/` would convert the most fragile component in the system
from "tuned by hand" to "regression tested."** Highest-value small task
available.

```bash
ffmpeg -i session.mov -vf "select='not(mod(n,3000))'" -vsync 0 \
  backend/tests/fixtures/frame-%02d.png
```

---

## Orientation

- `backend/app/constants.py` — units, panel grid, tolerances, flags. Single
  source of truth; the API republishes it at `GET /api/metadata` so the frontend
  never restates a unit.
- `backend/app/schema.sql` — note `face_to_path` is a GENERATED column, so an
  OCR'd value cannot be persisted even by mistake.
- `backend/app/pipeline/runner.py` — the stage sequence, deliberately a plain
  function rather than an orchestration framework (spec §4).
- `backend/app/analysis.py` — the five §7 views. Median not mean, sample size on
  every aggregate, confirmed shots only.

## Gotchas

- SQLite connections are opened with `check_same_thread=False` on purpose:
  FastAPI runs sync dependencies and sync endpoints on different threadpool
  threads. Removing it 500s every endpoint while the in-process test client
  stays green. See `test_threading.py`.
- Analysis reads `confidence = 'high'` only, and that is raised in exactly one
  place: `store.confirm_session`. If a view looks empty, the session is probably
  unconfirmed.
