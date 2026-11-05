# Fits

A one-page room critic for a week of San Francisco apartment tours. Listing sites show a price and a wide-angle photo. Neither says whether you can work, sleep, and cook there.

Fits searches Craigslist (and reads a pasted Zillow URL when search is blocked), ranks the words, measures the winning photo, and edits in the desk that is missing.

**100% new work on Nov 4, 2026.** Indie track. Theme: **Build for Your World.** Meta Global AI Developer Hackathon.

## The three models

All calls go to `https://api.meta.ai/v1` with `Authorization: Bearer $MODEL_API_KEY`. No other model, embeddings, TTS, or vision API runs at runtime.

| Model | Call | What it returns |
| --- | --- | --- |
| `muse-spark-1.3` | `POST /v1/responses` | A JSON ranking. Each listing gets a score and a one-sentence reason from the **description and location text only**. This pass does not see pixels. A second call returns the verdict: `fit`, `maybe`, or `no`, four bullets that cite the description, the neighborhood, and the measurements, and three questions for the leasing agent. |
| `sam-3.1` | `POST /v1/responses` | Boxes and masks for five noun prompts on the **winning photo only**: window, desk, bed, kitchen sink, closet. Five calls run in parallel. Local Pillow code turns each box into a percent of the image. An empty mask is a finding ("no desk"), not an error. |
| `muse-image-1.0` | `POST /v1/images/edits` | One edited image: a narrow standing desk in the window light. Furniture only. No people. |

**Live:** https://fits-gamma.vercel.app (Vercel, `MODEL_API_KEY` set on the project)

## Quickstart

```bash
cd ~/code/fits
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# put MODEL_API_KEY in .env — see .env.example
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

- **Find rooms** runs one Craigslist SF housing search (rent cap, neighborhood, cap of 8). Zillow search is best-effort. If Zillow returns a block page, the status line says so. Paste a Zillow listing URL and search again.
- **Replay** renders `demo/trace/` from disk. Zero calls to Craigslist, Zillow, or the Meta API. The photo is a room drawn for this demo, not a scraped listing photo.

Listing photos fetched during a live search stay in that response. They are not written into the repo.

## .env.example

```
MODEL_API_KEY=
```

Copy it to `.env`. `.env` is gitignored. The page still opens on Replay when the key is missing: live search returns listings, and ranking, SAM, and the edit wait for the key.

## 90-second demo script

Say this. The first line is the first line.

> This project was built from scratch during the hackathon. It's called Fits. I'm touring San Francisco apartments this week, and a listing gives me a price and a wide-angle photo. Neither tells me if I can work, sleep, and cook there.
>
> I set a rent cap, the Mission, and three must-haves: a desk, a real window, and a bed. One click searches Craigslist. Zillow's search page comes back as a captcha, so a pasted Zillow URL is the other way in.
>
> Muse Spark, muse-spark-1.3, ranks those listings from the description and the location text. It does not see pixels, and it marks a winner.
>
> SAM 3.1 runs only on that photo. Five prompts: window, desk, bed, kitchen sink, closet. The app measures each box as a percent of the image. An empty mask is a finding — no desk — not an error.
>
> A second Muse Spark call writes the verdict: fit, maybe, or no, four bullets that cite the description, the neighborhood, and the measurements, and three questions for the leasing agent.
>
> Muse Image, muse-image-1.0, edits that same photo: a narrow standing desk in the window light. Furniture only. No people.
>
> Replay shows the whole critic from a drawn fixture, with no key and no network. Indie track. Build for Your World.

Record the screen yourself. Do not submit the form from this repo.

## Paste-ready submission

**Track:** Indie

**Theme:** Build for Your World

**One line:** Fits tells you if a San Francisco listing is a room you can work, sleep, and cook in — not just a price and a wide photo.

**Use of Meta products:** Fits calls three models on the Meta Model API and nothing else. muse-spark-1.3 returns a JSON ranking from each listing's description and location, then a second call returns a fit / maybe / no verdict, four bullets, and three questions for the leasing agent. sam-3.1 returns boxes and masks for window, desk, bed, kitchen sink, and closet on the winning photo only; the app measures box area locally, and an empty mask is a finding. muse-image-1.0 returns one edited image that adds a narrow standing desk, furniture only, no people.

**Public repo:** https://github.com/lucas-chu/fits

**Video:** record from the script above and paste the URL on the Cerebral Valley form. The form needs your login; this project does not submit it.
