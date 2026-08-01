# Handoff — where this stands and what to do next

Written for a fresh session (human or Claude) with no prior context. Read this,
then `README.md` for the architecture.

**Branch:** `claude/new-session-miyh25` (not `main`)
**Last commit:** see `git log -1` — the media pipeline now runs on synthetic media
**Status:** 200 tests passing, frontend builds clean, phases 1 and 5 working end
to end. Phases 2–4 now run end to end against a *synthetic* session
(`backend/tests/synthetic.py`) built to the geometry and timing measured off
real frames — stages 1, 2, 3 and 7 are exercised for real. Still never run
against actual footage.

---

## The one-line summary

Everything verifiable without real footage or an API key **is** verified,
including the media pipeline against a synthetic session. The next action is to
run it against actual footage — the synthetic harness cannot validate detector
*tuning*, only structure.

Note that the sandbox needs no system ffmpeg: `pip install imageio-ffmpeg`
supplies a static binary, and `probe()` falls back to OpenCV when ffprobe is
absent. So the media tests run anywhere.

## Four bugs the synthetic harness found

All fixed, all would have fired on real footage:

1. **Panel cropped off its own left edge.** Otsu finds the *illuminated* screen,
   but the stat panel is a dark overlay — so the detected screen began exactly
   where the panel ended, and the panel crop contained sky. Recovered by
   measuring the band where the panel would be and extending only if it contains
   bright digits (brightness alone can't tell panel from unlit bay).
2. **Change detection could never fire.** Mean-absolute-difference over a
   downscaled crop: a full set of new numbers is thin strokes over mostly flat
   background, measuring ~0.001 against a threshold of 0.12. Replaced with
   *fraction of pixels visibly changed*, at working resolution.
3. **Onset post-filter discarded real shots.** It kept onsets above
   `median + sigma` of the *detected onsets* — which, when detection is clean and
   impacts are similar in loudness, sits above most of them. Kept 1 of 4.
4. **`onset_detect(delta=...)` is a fraction of the loudest onset, not an
   absolute.** librosa max-normalizes the envelope first, so `delta=0.6` meant
   "must reach 60% of the session's loudest impact" and silently dropped quieter
   ones — i.e. exactly the mishits that strike-location analysis exists to study.
   Now 0.08, with speech rejection done explicitly afterwards.

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

It prints one line per stage. Stages 1–3 now work on synthetic media, so the
likely failure mode is a *count* mismatch rather than a crash: too many or too
few detected shots, which is threshold tuning (see open question 3), not a
design fault. If stage 2 reports `needs_calibration`, that is designed
behaviour — spec §4 says never guess a crop. Capture the output either way.

The synthetic harness is reusable for reproducing any bug you find:

```python
from backend.tests import synthetic
s = synthetic.build("/tmp/s.mp4", shots=4)   # returns ground truth alongside
```

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
3. **Do the detector thresholds hold on real audio and video?**
   `ONSET_PEAK_DELTA`, `ONSET_RELATIVE_FLOOR`, `PANEL_CHANGE_THRESHOLD` and
   `PANEL_PIXEL_DELTA` in `constants.py` are calibrated against synthetic media.
   The *structure* is now proven; the *numbers* are guesses until real footage
   runs. Expect to retune, and treat a wrong count as tuning rather than a
   design fault.
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
| Ingest, probe, audio extraction | Verified on synthetic media — 12 tests |
| Screen detection, panel derivation | Verified on synthetic media |
| Panel change detection | Verified on synthetic media |
| Impact onset detection | Verified on synthetic media |
| Detector thresholds on *real* media | **Never run** — expect retuning |
| Whisper transcription | **Never run** |
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
