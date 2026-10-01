# Getting started on Windows

This guide gets Medical Concierge running on a Windows desktop with as
little typing as possible. Total time: about 10 minutes, most of it
downloads. You do **not** need to know how to code or use a command prompt.

## What you'll need

1. A Windows 10 or 11 PC.
2. An **Anthropic API key** — this is what lets the app read your documents
   (photos of pill bottles, PDFs, handwritten notes). It's a paid service
   billed by usage; reading a typical document costs a few cents.
3. About 10 minutes.

---

## Step 1 — Install Python (one time)

Python is the free software this app runs on.

1. Go to <https://www.python.org/downloads/> and click the big
   **Download Python** button.
2. Run the installer.
3. **Important:** on the first screen, tick the checkbox that says
   **"Add python.exe to PATH"** before clicking Install. This is the one
   step people miss.
4. Click **Install Now** and let it finish.

## Step 2 — Get an Anthropic API key (one time)

1. Go to <https://console.anthropic.com> and create an account (or sign in).
2. Add a payment method under **Settings → Billing** (usage-based; a few
   dollars of credit lasts a long time for personal document reading).
3. Go to **Settings → API keys**, click **Create key**, and copy it
   somewhere safe. It looks like `sk-ant-...`. Treat it like a password.

## Step 3 — Download Medical Concierge (one time)

1. On the project's GitHub page, click the green **Code** button, then
   **Download ZIP**.
2. Right-click the downloaded ZIP → **Extract All…** and extract it
   somewhere easy to find, like `Documents\MedicalConcierge`.

## Step 4 — Start it

1. Open the extracted folder and **double-click `Start-MedicalConcierge.bat`**.
2. The first run takes about a minute — it sets itself up, then asks you to
   paste your API key. Right-click to paste in that window, then press Enter.
3. Your browser opens to the app automatically. A welcome screen explains
   what the app does, where its information comes from, and how to get the
   best results — read it once, click **Get started**, and it won't show
   again. (You can reopen it any time with **How this works** at the top of
   the page.) That's it.

A black window stays open in the background — that **is** the app running.
Leave it open while you use Medical Concierge; close it when you're done.
Your records are saved automatically on your computer.

**Every time after the first:** just double-click `Start-MedicalConcierge.bat`
again. It starts in a few seconds and opens your browser.

> **Windows may show "Windows protected your PC"** the first time (because
> the launcher isn't from a registered publisher). Click **More info →
> Run anyway**. You can read the launcher yourself — it's a plain text file.

### Optional: make it feel like a real app

Right-click `Start-MedicalConcierge.bat` → **Send to → Desktop (create
shortcut)**. Now it launches from your desktop like any other program.
(Right-click the shortcut → Properties → Change Icon to give it a nicer icon.)

---

## Using the app

### Lab results

If you upload a lab report, the app reads the values off it the same way it
reads a medication chart, and shows them in their own card with the
high/low flags the report itself printed. This matters more than it sounds:
several of the panel's experts reason about kidney function, liver enzymes
and INR, and without real numbers they can only tell you what they are
assuming.

### The expert panel

Below the warnings there is an **Expert panel review**. The warnings above it
come from a fixed reference table — fast, free, and the same for everyone.
The panel is different: a group of AI subject-matter experts (pharmacists,
organ-system specialists, a deprescribing voice, a skeptic whose whole job is
arguing that most warnings are noise, and someone speaking from lived
experience) each review your list on their own, then argue with each other,
then report what they could **not** agree on.

Add a note first if anything has changed — "exhausted since the new
prescription in March", "bruising more easily lately". The panel reads it,
and it makes the review far more specific.

A run takes a couple of minutes and costs a few cents of API usage. You will
see the rounds tick by as it works. At the end you get what the panel agreed
on, where it split (kept deliberately — that is usually the part worth asking
about), and a list of questions to ask, each marked with whether it is free,
a pharmacist question, or something for your next appointment. **PDF of these
questions** gives you a page to take with you.

### Answering the panel's open questions

After a review you will see **Open questions the panel is waiting on** — the
specific things it said would settle whatever it could not agree on. "Whether
an INR drawn now differs from the last one." "Whether the warfarin bottle
says 5 mg."

Go and find out, type the answer in, and press Save. Next time you convene
the panel it treats your answer as established fact, and any expert whose
stated conditions it meets has to concede — you will see a green **Settled**
block saying what changed and who changed their mind. That is the point of
the whole thing: each round of this should leave you with fewer open
questions than the last, not the same ones restated.

If a question does not apply to you, press **Not applicable** and it stops
coming back.

Two things worth knowing: every expert in the panel is generated by the same
underlying AI model, so several of them agreeing is much weaker evidence than
several real clinicians agreeing — the review says this itself. And nothing
it produces is ever an instruction to change what you take; it is built to
hand you better questions, not answers.



- **Add a document:** drag the file in and click **Read this document** —
  that's it. Medicines and supplements are found together automatically, and
  printed, handwritten, and bottle-photo pages are recognized on their own;
  there is nothing to select. Photos from your phone work — email them to
  yourself or use any transfer method, then drop them in.
- **Your medication list:** the list at the top builds itself as you add
  documents (the same medicine seen in several documents becomes one entry),
  and you can add items by hand. Click **Edit** to fix a dose or add a note,
  **Stop** when you no longer take something (it's kept, greyed out — never
  deleted), and **History** to see every change ever made to that entry.
  An **Original baseline** snapshot is saved automatically when your list
  first gets content; press **Set new baseline** whenever your regimen is
  confirmed correct, and **Compare to baseline** to see exactly what was
  added, stopped, or changed since. Warnings always reflect the list — stop
  a medicine and its warnings clear immediately.
- **Warnings & recommendations:** every time you add a document, all your
  records are automatically checked against each other — drug-drug
  interactions, supplement and vitamin/mineral interactions, duplicate
  medicines from different doctors, and nutrients your medicines may
  deplete. Warnings appear at the top, color-coded (red = major), each with
  a plain-language explanation and a suggested next step. They also appear
  on the doctor PDF. This is a built-in starter screen, not a complete
  interaction check — your pharmacist can run a full one.
- **Confidence badges:** every record shows how confident the app is that it
  read the document correctly. Anything marked **"check this"** deserves a
  quick look — the app read something it wasn't sure about (usually messy
  handwriting) and says exactly what was unclear.
- **Share with your doctor:** the **PDF for your doctor** button creates a
  clean one-page-per-visit summary — medications and supplements with doses,
  frequencies, and anything the app wasn't sure about marked "PLEASE
  CONFIRM" in red. Print it or bring it on your phone. **CSV** gives you a
  spreadsheet; **JSON** is a complete backup of everything.

## Where your data lives

Everything stays on your PC — records are stored in a single file:
`backend\medconcierge.sqlite3` inside the app folder. Copy that file to a
USB drive or personal backup to back everything up. No account, no cloud
storage, nothing leaves your machine except:

- the document images sent to Anthropic's API for reading, and
- medicine names sent to the U.S. National Library of Medicine (RxNorm) to
  standardize them.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Python was not found" | Redo Step 1 and make sure you ticked **Add python.exe to PATH**. Then restart the launcher. |
| Yellow "Setup needed" banner in the app | Your API key isn't set. Close the black window, delete the file `backend\.env`, and double-click the launcher again to re-enter it. |
| Browser opens before the app is ready ("can't connect") | Wait a few seconds and refresh the page. |
| "Something went wrong" when reading a document | Check the black window for the real error. Most common: an invalid API key, or no internet connection. |
| "File too large" | Photos are shrunk automatically, so this should be rare. Single files over 30 MB are rejected — split a huge PDF, or re-save the file as JPG. PDFs are limited to 50 pages. |
| Want to start completely fresh | Click **Start over…** (top-right). A PDF archive of everything is downloaded first, then all data is erased — the wipe only happens after the archive exists. |
| Windows SmartScreen blocks the launcher | Click **More info → Run anyway** (see note in Step 4). |

## Updating to a new version

Download the new ZIP, extract it to a **new** folder, and copy your old
`backend\medconcierge.sqlite3` and `backend\.env` files into the new
folder's `backend` directory. Then launch as usual.
