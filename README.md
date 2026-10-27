# CV-generator-AI-powered-
# 📄 AI CV Generator

An AI-powered CV (resume) builder. You chat with an AI interviewer powered by **Grok (xAI)**, answer a few simple questions, and download a clean, professionally formatted, ATS-friendly **PDF CV** that is ready to send to employers.

Built with **Streamlit** (UI), **Grok API** (conversation + content writing) and **ReportLab** (PDF generation).

---

## ✨ Features

- **Photo upload** from your device (PNG/JPG). Automatically corrected for rotation, cropped to a square and placed in the CV header. Optional.
- **Guided AI interview** (the *Start conversation* button). The agent asks for everything a good CV needs, a few questions at a time:
  full name, email, phone, location, LinkedIn/website, target role, work experience (with achievements), education, skills, certifications, projects, languages and awards.
- **One-click CV generation.** Grok turns your answers into structured, polished CV content (action-verb bullets, consistent dates, a professional summary) and a PDF is produced instantly.
- **Formal, job-ready layout**: single column, standard section headings, real selectable text (so Applicant Tracking Systems can read it), clickable email/LinkedIn links, automatic multi-page handling with page numbers.
- **Customisable style**: 4 accent colours and A4 / US Letter page size.
- **Honest by design**: the AI is instructed never to invent employers, dates, numbers or skills. If you did not provide something, it is simply left out.
- **Iterate freely**: keep chatting to add or fix details, then click *Generate CV* again.

---

## 🧭 How it works

```
 Upload photo (optional)
        │
        ▼
 Start conversation ──► Grok interviews you (chat)
        │
        ▼
 Generate CV ──► Grok converts the transcript into structured JSON
        │
        ▼
 Python validates and cleans the data ──► ReportLab renders the PDF ──► Download
```

1. **Interview** – A system prompt turns Grok into a friendly career coach that asks 2–3 related questions at a time and signals when it has everything.
2. **Extraction** – When you click *Generate CV*, the full transcript is sent to Grok with a strict JSON schema. The response is parsed defensively (handles code fences, stray text, wrong types).
3. **Rendering** – The cleaned data is laid out with ReportLab (Platypus) into a one- or two-page PDF.

xAI's API is OpenAI-compatible, so the app uses the official `openai` Python SDK pointed at `https://api.x.ai/v1`.

---

## 📁 Project structure

```
ai-cv-generator/
├── app.py            # Streamlit app: UI, Grok calls, data cleaning, PDF builder
├── requirements.txt  # Python dependencies
└── README.md         # This file
```

---

## 🚀 Run locally

**Requirements:** Python 3.9+ and an xAI API key (get one at <https://console.x.ai>).

```bash
# 1. Clone your repo
git clone https://github.com/<your-username>/ai-cv-generator.git
cd ai-cv-generator

# 2. (Recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Provide your API key (choose ONE option)
#    a) Environment variable
export XAI_API_KEY="xai-your-key-here"      # Windows PowerShell: $env:XAI_API_KEY="xai-..."
#    b) Streamlit secrets file: create .streamlit/secrets.toml containing
#         XAI_API_KEY = "xai-your-key-here"
#    c) Or just paste the key into the app's sidebar when it opens

# 5. Run
streamlit run app.py
```

The app opens at <http://localhost:8501>.

---

## ☁️ Deploy on Streamlit Community Cloud

1. **Push the project to GitHub** (see the `.gitignore` note below first).
2. Go to <https://share.streamlit.io> and sign in with GitHub.
3. Click **Create app** → choose your repository and branch, and set **Main file path** to `app.py`.
4. Open **Advanced settings → Secrets** and paste:
   ```toml
   XAI_API_KEY = "xai-your-key-here"
   # Optional: override the default model
   # XAI_MODEL = "grok-4-1-fast-non-reasoning"
   ```
5. Click **Deploy**. Streamlit installs `requirements.txt` automatically.

> **Public app = shared credits.** If you store your own key in Secrets, anyone with the link can use your xAI credits. Options: keep the app private (Share settings), set a spending limit in the xAI console, or leave `XAI_API_KEY` unset so each visitor pastes their own key in the sidebar.

### ⚠️ Never commit your API key

Create a `.gitignore` in the project root before your first commit:

```gitignore
.venv/
__pycache__/
.streamlit/secrets.toml
.env
*.pdf
```

---

## ⚙️ Configuration

| Setting | Where | Default | Description |
|---|---|---|---|
| `XAI_API_KEY` | Streamlit secret / env var / sidebar | – | Your xAI API key (required). |
| `XAI_MODEL` | Streamlit secret / env var / sidebar | `grok-4-1-fast-non-reasoning` | Any chat model on your xAI account. Model names change over time; check <https://docs.x.ai> if you get a "model not found" error. |
| Accent colour | Sidebar | Navy | Navy, Charcoal, Forest Green or Burgundy. |
| Page size | Sidebar | A4 | A4 or US Letter. |

---

## 🛠️ Customising the app

All of this lives in `app.py`:

- **Interview questions / tone** → edit `INTERVIEW_SYSTEM_PROMPT`.
- **What the AI extracts and how it words bullets** → edit `EXTRACTION_SYSTEM_PROMPT`.
- **Colours** → `ACCENT_COLORS`. **Page sizes** → `PAGE_SIZES`.
- **Layout, fonts, spacing, section order** → `_styles()` and `build_cv_pdf()`.
- **Default model** → `DEFAULT_MODEL`.

---

## 🧯 Troubleshooting

| Problem | Fix |
|---|---|
| "Your xAI API key was rejected" | Re-copy the key from console.x.ai; make sure it has not been revoked. |
| "The selected model was not found" | Change the model name in the sidebar to one listed at docs.x.ai. |
| "Rate limit or credit limit reached" | Check your xAI credits/limits, then retry. |
| "I could not find your name yet" | Tell the interviewer your full name in the chat, then click *Generate CV* again. |
| Photo is rejected | Use a valid PNG or JPG file. |
| Some characters are missing in the PDF | See *Limitations* below. |

---

## ⚠️ Limitations

- **Latin-script text only in the PDF.** The built-in PDF fonts cannot draw Arabic/Urdu/Pashto, Chinese, Cyrillic, etc. Accented Latin letters (é, ü, ñ …) are fine; unsupported characters are transliterated or dropped instead of showing as black boxes. To support more scripts, register a Unicode TTF font (e.g. Noto Sans) with ReportLab's `pdfmetrics.registerFont` and use it in `_styles()`.
- **AI can make mistakes.** Always read the generated CV before sending it. Use the *Review the data* panel to check what was extracted.
- **No persistence.** Conversations and CVs live only in the browser session and are not saved by the app.
- **Privacy.** Your answers are sent to the xAI API to generate the CV. Do not share anything you are not comfortable sending to a third-party AI provider.

---

## 💡 Ideas for future improvements

Multiple CV templates, cover-letter generation, tailoring the CV to a pasted job description, editable CV preview before export, DOCX export, Unicode font support, and multi-language CVs.

---

## 📜 License

Released under the MIT License. Add a `LICENSE` file to your repository if you want to make this explicit.
