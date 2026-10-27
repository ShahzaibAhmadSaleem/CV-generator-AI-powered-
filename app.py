
"""
AI CV Generator
===============
A Streamlit app that interviews the user with an LLM (Groq or xAI Grok) and turns
the answers into a clean, ATS-friendly, professionally formatted PDF CV.

Flow:  upload photo (optional)  ->  start conversation  ->  answer questions
       ->  click "Generate CV"  ->  download PDF

Run locally:   streamlit run app.py
"""

from __future__ import annotations

import io
import json
import os
import re
import unicodedata
from typing import Any
from xml.sax.saxutils import escape

import streamlit as st
from openai import BadRequestError, OpenAI
from PIL import Image, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Both providers expose an OpenAI-compatible API, so the one `openai` SDK serves both.
# NOTE: "Groq" (groq.com, keys start with gsk_) and "Grok" (xAI, keys start with
# xai-) are two different companies with different endpoints and API keys.
# Model names change over time, so the app loads the list of models your key can
# use straight from the provider; "default_model" is only the fallback.
PROVIDERS: dict[str, dict[str, str]] = {
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL",
        "key_prefix": "gsk_",
        "model_prefix": "",
        "console": "console.groq.com/keys",
        "models_doc": "console.groq.com/docs/models",
    },
    "xAI (Grok)": {
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-4.3",
        "key_env": "XAI_API_KEY",
        "model_env": "XAI_MODEL",
        "key_prefix": "xai-",
        "model_prefix": "grok",
        "console": "console.x.ai",
        "models_doc": "docs.x.ai",
    },
}
DEFAULT_PROVIDER = "Groq"
# Skip models that cannot do text chat (speech, images, embeddings, safety classifiers...).
NON_CHAT_HINTS = (
    "image", "imagine", "video", "embed", "tts", "stt", "voice", "aurora",
    "whisper", "guard", "orpheus", "playai", "transcri", "moderation", "safeguard",
)

INTERVIEW_DONE_MARKER = "[[INTERVIEW_COMPLETE]]"

ACCENT_COLORS = {
    "Navy": "#1F3A5F",
    "Charcoal": "#2B2B2B",
    "Forest Green": "#1E4D3A",
    "Burgundy": "#6D1F2F",
}
PAGE_SIZES = {"A4": A4, "US Letter": LETTER}

INTERVIEW_SYSTEM_PROMPT = f"""
You are a friendly, professional career coach interviewing a person so you can
write their CV (resume). Your job is ONLY to collect accurate information.

STYLE
- Warm, concise, encouraging. Plain text with light markdown at most.
- Ask at most 2-3 closely related questions per message. Never dump a long form.
- Reply in English unless the user clearly prefers another language.

TOPICS TO COVER (roughly in this order)
1. Contact details: full name, email address, phone number, city and country,
   LinkedIn / portfolio / website (optional).
2. Target job title or industry they are applying for.
3. Short professional background (what they do, years of experience, strengths).
4. Work experience - for EACH job: job title, company, location, start and end
   dates (or "Present"), main responsibilities and achievements. Encourage
   measurable results (numbers, percentages, team size, budgets). Ask if there
   is another job before moving on. If they are a student or fresher, focus on
   internships, projects and volunteering instead.
5. Education - degree, institution, location, dates, grade/GPA/honours (optional).
6. Skills - technical skills, tools, soft skills.
7. Certifications and training - name, issuing organisation, year.
8. Projects (optional) - name, what it does, technologies, result.
9. Languages spoken and proficiency level.
10. Awards, publications, volunteering or other achievements (optional).

RULES
- If an email or phone number looks invalid, politely ask the user to re-check it.
- Accept "skip", "none" or "n/a" and move on.
- NEVER invent or assume facts. Do not write the CV yourself in this chat.
- When you have covered everything (or the user says they are done), write a very
  short recap, tell them to click the "Generate CV" button below, and end that
  final message with the exact text {INTERVIEW_DONE_MARKER}
""".strip()

EXTRACTION_SYSTEM_PROMPT = """
You convert an interview transcript into structured CV data.
Return ONE valid JSON object and nothing else (no markdown, no commentary).

Schema (use "" for unknown strings and [] for unknown lists):
{
  "full_name": "",
  "headline": "target job title or professional headline, e.g. 'Senior Data Analyst'",
  "email": "",
  "phone": "",
  "location": "City, Country",
  "linkedin": "",
  "website": "",
  "summary": "2-4 sentence professional summary",
  "experience": [
    {"title": "", "company": "", "location": "", "start_date": "Mon YYYY",
     "end_date": "Mon YYYY or Present", "bullets": ["", ""]}
  ],
  "education": [
    {"degree": "", "institution": "", "location": "", "start_date": "",
     "end_date": "", "details": "GPA, honours, relevant coursework"}
  ],
  "skills": [{"category": "e.g. Technical Skills", "items": ["", ""]}],
  "certifications": [{"name": "", "issuer": "", "date": ""}],
  "projects": [{"name": "", "technologies": "", "description": "", "bullets": [""]}],
  "languages": [{"language": "", "proficiency": ""}],
  "achievements": [""]
}

Rules:
- Use ONLY facts stated by the candidate. Never invent employers, dates, degrees,
  numbers, or skills. If something was not provided, leave it empty.
- Polish the wording: fix spelling/capitalisation, use concise professional
  language and strong action verbs (past tense for past roles, present tense for
  the current role). 2-5 bullets per job, each starting with an action verb.
  Keep any metrics exactly as the candidate gave them.
- The summary must be built only from the information provided.
- List experience and education in reverse-chronological order.
- Normalise dates to "Mon YYYY" (e.g. "Jan 2021"); use "Present" for current roles.
- Group skills into sensible categories (e.g. Technical Skills, Tools, Soft Skills).
""".strip()


# --------------------------------------------------------------------------- #
# LLM helpers (Groq / xAI)
# --------------------------------------------------------------------------- #

def get_client(api_key: str, base_url: str) -> OpenAI:
    """Groq and xAI are OpenAI-compatible, so we use the OpenAI SDK with a custom base URL."""
    return OpenAI(api_key=api_key, base_url=base_url, timeout=120.0)


def call_llm(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool = False,
    temperature: float = 0.4,
) -> str:
    """
    Send a chat completion request and return the text of the reply.
    If the API rejects an optional parameter (JSON mode, temperature) with a 400,
    retry without it. Key / model problems are raised straight away.
    """
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    droppable = [k for k in ("response_format", "temperature") if k in kwargs]
    while True:
        try:
            response = client.chat.completions.create(**kwargs)
            return (response.choices[0].message.content or "").strip()
        except BadRequestError as exc:
            low = (api_message(exc) + " " + str(exc)).lower()
            fatal = any(t in low for t in ("api key", "not found", "does not exist", "no access", "decommissioned"))
            if fatal or not droppable:
                raise
            kwargs.pop(droppable.pop(0), None)


def api_message(exc: Exception) -> str:
    """Pull the human-readable message out of an API error (its JSON shape varies by provider)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        for candidate in (err, body.get("message"), body.get("code")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    text = getattr(getattr(exc, "response", None), "text", "")
    if isinstance(text, str) and text.strip():
        return text.strip()[:300]
    return str(exc)


def friendly_error(exc: Exception, provider: str = "The AI provider") -> str:
    """Turn API exceptions into messages a normal user can act on."""
    name = type(exc).__name__
    detail = api_message(exc)
    low = (detail + " " + str(exc)).lower()
    cfg = PROVIDERS.get(provider, {})
    where = cfg.get("console", "your provider's console")
    if name == "AuthenticationError" or "api key" in low:
        return (
            f"{provider} rejected your API key. Re-copy it from {where} (no quotes or spaces) and make "
            f"sure the selected provider matches the key. {provider} said: {detail}"
        )
    if name == "RateLimitError":
        return f"{provider} rate limit or quota reached. Wait a minute and retry, or check your usage limits. {provider} said: {detail}"
    if name == "PermissionDeniedError":
        return f"Your key does not have permission for this request (or the account has no credits). {provider} said: {detail}"
    model_gone = any(t in low for t in ("not found", "does not exist", "deprecated", "decommissioned", "invalid"))
    if name == "NotFoundError" or ("model" in low and model_gone):
        return f"That model is not available for your key. Pick another model in the sidebar. {provider} said: {detail}"
    if name in {"APIConnectionError", "APITimeoutError"}:
        return f"Could not reach {provider}. Please check your connection and try again."
    status = getattr(exc, "status_code", None)
    return f"{provider} API error{f' ({status})' if status else ''}: {detail}"


def list_chat_models(api_key: str, base_url: str, model_prefix: str = "") -> list[str]:
    """Ask the provider which chat models this key can use (raises on failure)."""
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=20.0)
    ids = [m.id for m in client.models.list()]
    chat = [
        i for i in ids
        if i.lower().startswith(model_prefix) and not any(h in i.lower() for h in NON_CHAT_HINTS)
    ]
    return sorted(set(chat), reverse=True)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse JSON from a model reply, tolerating code fences and stray text."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("The AI did not return valid CV data. Please try again.")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("The AI returned malformed CV data. Please try again.") from exc
    if not isinstance(data, dict):
        raise ValueError("The AI returned unexpected CV data. Please try again.")
    return data


def build_transcript(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages:
        if m.get("hidden"):
            continue
        who = "INTERVIEWER" if m["role"] == "assistant" else "CANDIDATE"
        lines.append(f"{who}: {m['content']}")
    return "\n\n".join(lines)


def extract_cv_data(client: OpenAI, model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    reply = call_llm(
        client,
        model,
        [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": "Interview transcript:\n\n" + build_transcript(messages)},
        ],
        json_mode=True,
        temperature=0.2,
    )
    return normalize_cv(parse_json_object(reply))


# --------------------------------------------------------------------------- #
# Data cleaning / normalisation
# --------------------------------------------------------------------------- #

def clean(value: Any) -> str:
    """
    Make text safe for ReportLab's built-in Helvetica font (WinAnsi / Latin-1 range).
    Accented Latin letters, curly quotes, dashes and bullets are kept; characters the
    font cannot draw are transliterated if possible, otherwise dropped (so you never
    get black boxes in the PDF).
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", text)
    text = text.replace("\u00a0", " ").replace("\u2011", "-").replace("\u2212", "-")
    out: list[str] = []
    for ch in text:
        if ch in "\r\n\t" or ord(ch) < 32:
            out.append(" ")
            continue
        try:
            ch.encode("cp1252")
            out.append(ch)
            continue
        except UnicodeEncodeError:
            pass
        base = "".join(
            c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)
        )
        for c in base:
            try:
                c.encode("cp1252")
                out.append(c)
            except UnicodeEncodeError:
                pass
    return re.sub(r"\s+", " ", "".join(out)).strip()


def as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def as_bullets(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [re.sub(r"^\s*[-*\u2022]\s*", "", line) for line in value.splitlines()]
    return [b for b in (clean(x) for x in as_list(value)) if b]


def normalize_cv(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce whatever the model returned into a predictable structure."""
    cv: dict[str, Any] = {
        key: clean(raw.get(key))
        for key in ("full_name", "headline", "email", "phone", "location", "linkedin", "website", "summary")
    }

    cv["experience"] = []
    for e in as_list(raw.get("experience")):
        if not isinstance(e, dict):
            continue
        item = {k: clean(e.get(k)) for k in ("title", "company", "location", "start_date", "end_date")}
        item["bullets"] = as_bullets(e.get("bullets"))
        if item["title"] or item["company"]:
            cv["experience"].append(item)

    cv["education"] = []
    for e in as_list(raw.get("education")):
        if not isinstance(e, dict):
            continue
        item = {k: clean(e.get(k)) for k in ("degree", "institution", "location", "start_date", "end_date")}
        details = e.get("details")
        item["details"] = "; ".join(as_bullets(details)) if isinstance(details, list) else clean(details)
        if item["degree"] or item["institution"]:
            cv["education"].append(item)

    skills: list[tuple[str, list[str]]] = []
    raw_skills = raw.get("skills")
    if isinstance(raw_skills, dict):
        for cat, items in raw_skills.items():
            vals = [clean(i) for i in as_list(items) if clean(i)]
            if vals:
                skills.append((clean(cat), vals))
    else:
        loose: list[str] = []
        for s in as_list(raw_skills):
            if isinstance(s, dict):
                vals = [clean(i) for i in as_list(s.get("items")) if clean(i)]
                if vals:
                    skills.append((clean(s.get("category")) or "Skills", vals))
            elif clean(s):
                loose.append(clean(s))
        if loose:
            skills.append(("Skills", loose))
    cv["skills"] = skills

    cv["certifications"] = []
    for c in as_list(raw.get("certifications")):
        if isinstance(c, dict):
            item = {k: clean(c.get(k)) for k in ("name", "issuer", "date")}
        else:
            item = {"name": clean(c), "issuer": "", "date": ""}
        if item["name"]:
            cv["certifications"].append(item)

    cv["projects"] = []
    for p in as_list(raw.get("projects")):
        if isinstance(p, dict):
            item = {k: clean(p.get(k)) for k in ("name", "technologies", "description")}
            item["bullets"] = as_bullets(p.get("bullets"))
        else:
            item = {"name": clean(p), "technologies": "", "description": "", "bullets": []}
        if item["name"]:
            cv["projects"].append(item)

    langs: list[str] = []
    for lang in as_list(raw.get("languages")):
        if isinstance(lang, dict):
            name, level = clean(lang.get("language")), clean(lang.get("proficiency"))
            if name:
                langs.append(f"{name} ({level})" if level else name)
        elif clean(lang):
            langs.append(clean(lang))
    cv["languages"] = langs

    cv["achievements"] = as_bullets(raw.get("achievements"))
    return cv


# --------------------------------------------------------------------------- #
# Photo handling
# --------------------------------------------------------------------------- #

def prepare_photo(file_bytes: bytes, size: int = 400) -> bytes:
    """Fix rotation, crop to a square (biased toward the top so faces stay in), resize, JPEG."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(file_bytes)))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = int((h - side) * 0.25) if h > w else (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# PDF generation (ReportLab)
# --------------------------------------------------------------------------- #

DATE_COL_WIDTH = 4.4 * cm
PHOTO_SIZE = 3.0 * cm


def _styles(accent: colors.Color) -> dict[str, ParagraphStyle]:
    dark = colors.HexColor("#222222")
    grey = colors.HexColor("#444444")
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=12.5, textColor=dark)
    return {
        "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=23, leading=27, textColor=accent),
        "headline": ParagraphStyle("headline", fontName="Helvetica", fontSize=11.5, leading=15, textColor=grey, spaceAfter=3),
        "contact": ParagraphStyle("contact", fontName="Helvetica", fontSize=9, leading=12.5, textColor=dark),
        "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=accent),
        "body": body,
        "role": ParagraphStyle("role", parent=body, fontName="Helvetica-Bold", fontSize=10),
        "date": ParagraphStyle("date", parent=body, alignment=TA_RIGHT, textColor=grey),
        "sub": ParagraphStyle("sub", parent=body, fontName="Helvetica-Oblique", textColor=grey),
        "bullet": ParagraphStyle(
            "bullet", parent=body, leftIndent=12, bulletIndent=2, spaceAfter=1.5, bulletFontName="Helvetica"
        ),
    }


def _p(text: str, style: ParagraphStyle, bullet: str | None = None) -> Paragraph:
    return Paragraph(escape(text), style, bulletText=bullet) if bullet else Paragraph(escape(text), style)


def _section_heading(title: str, st_: dict[str, ParagraphStyle], width: float, accent: colors.Color) -> Table:
    table = Table(
        [[Paragraph(escape(title.upper()), st_["section"])]],
        colWidths=[width],
        spaceBefore=9,
        spaceAfter=4,
    )
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 0.9, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    table.keepWithNext = True  # never leave a heading stranded at the bottom of a page
    return table


def _row(left: Paragraph, right: Paragraph, width: float) -> Table:
    """Two-column row: title on the left, dates right-aligned."""
    table = Table([[left, right]], colWidths=[width - DATE_COL_WIDTH, DATE_COL_WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _date_range(start: str, end: str) -> str:
    if start and end:
        return f"{start} \u2013 {end}"
    return start or end


def _join(*parts: str, sep: str = ", ") -> str:
    return sep.join(p for p in parts if p)


def _url(value: str) -> str:
    value = clean(value).replace(" ", "")
    if value and not re.match(r"^(https?://|mailto:)", value, re.IGNORECASE):
        value = "https://" + value
    return value


def _link(href: str, label: str, accent_hex: str) -> str:
    safe_href = escape(href, {'"': "&quot;"})
    return f'<a href="{safe_href}" color="{accent_hex}">{escape(label)}</a>'


def _contact_lines(cv: dict[str, Any], accent_hex: str) -> list[str]:
    line1: list[str] = []
    if cv["location"]:
        line1.append(escape(cv["location"]))
    if cv["phone"]:
        line1.append(escape(cv["phone"]))
    if cv["email"]:
        line1.append(_link("mailto:" + cv["email"].replace(" ", ""), cv["email"], accent_hex))
    line2: list[str] = []
    for key in ("linkedin", "website"):
        if cv[key]:
            label = re.sub(r"^https?://(www\.)?", "", cv[key].strip(), flags=re.IGNORECASE).rstrip("/")
            line2.append(_link(_url(cv[key]), label, accent_hex))
    sep = "&nbsp;&nbsp;|&nbsp;&nbsp;"
    return [sep.join(line) for line in (line1, line2) if line]


def build_cv_pdf(
    cv: dict[str, Any],
    photo_bytes: bytes | None = None,
    accent_hex: str = "#1F3A5F",
    pagesize: tuple[float, float] = A4,
) -> bytes:
    """Render the normalised CV dict to a PDF and return the raw bytes."""
    accent = colors.HexColor(accent_hex)
    s = _styles(accent)
    margin = 1.8 * cm
    # SimpleDocTemplate's frame has 6pt padding per side; tables must match the
    # inner width or they stick out past the paragraph text.
    width = pagesize[0] - 2 * margin - 12
    story: list[Any] = []

    # ---- Header (name, headline, contact, optional photo) ----
    header: list[Any] = [Paragraph(escape(cv["full_name"] or "Your Name"), s["name"])]
    if cv["headline"]:
        header.append(Paragraph(escape(cv["headline"]), s["headline"]))
    for line in _contact_lines(cv, accent_hex):
        header.append(Paragraph(line, s["contact"]))

    if photo_bytes:
        photo = RLImage(io.BytesIO(photo_bytes), width=PHOTO_SIZE, height=PHOTO_SIZE)
        photo.hAlign = "RIGHT"
        head_table = Table([[header, photo]], colWidths=[width - PHOTO_SIZE - 0.4 * cm, PHOTO_SIZE + 0.4 * cm])
        head_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story.append(head_table)
    else:
        story.extend(header)

    def heading(title: str) -> None:
        story.append(_section_heading(title, s, width, accent))

    # ---- Section builders ----
    def summary_section() -> None:
        if cv["summary"]:
            heading("Professional Summary")
            story.append(_p(cv["summary"], s["body"]))

    def experience_section() -> None:
        if not cv["experience"]:
            return
        heading("Professional Experience")
        for e in cv["experience"]:
            head = _row(
                _p(e["title"] or e["company"], s["role"]),
                _p(_date_range(e["start_date"], e["end_date"]), s["date"]),
                width,
            )
            block: list[Any] = [head]
            sub = _join(e["company"] if e["title"] else "", e["location"])
            if sub:
                block.append(_p(sub, s["sub"]))
            bullets = [_p(b, s["bullet"], "\u2022") for b in e["bullets"]]
            story.append(KeepTogether(block + bullets[:1]))
            story.extend(bullets[1:])
            story.append(Spacer(1, 5))

    def education_section() -> None:
        if not cv["education"]:
            return
        heading("Education")
        for e in cv["education"]:
            head = _row(
                _p(e["degree"] or e["institution"], s["role"]),
                _p(_date_range(e["start_date"], e["end_date"]), s["date"]),
                width,
            )
            block: list[Any] = [head]
            sub = _join(e["institution"] if e["degree"] else "", e["location"])
            if sub:
                block.append(_p(sub, s["sub"]))
            if e["details"]:
                block.append(_p(e["details"], s["body"]))
            story.append(KeepTogether(block))
            story.append(Spacer(1, 4))

    def skills_section() -> None:
        if not cv["skills"]:
            return
        heading("Skills")
        for category, items in cv["skills"]:
            text = f"<b>{escape(category)}:</b> {escape(', '.join(items))}"
            story.append(Paragraph(text, ParagraphStyle("skill", parent=s["body"], spaceAfter=2)))

    def certifications_section() -> None:
        if not cv["certifications"]:
            return
        heading("Certifications")
        for c in cv["certifications"]:
            left = f"<b>{escape(c['name'])}</b>" + (f" \u2013 {escape(c['issuer'])}" if c["issuer"] else "")
            story.append(_row(Paragraph(left, s["body"]), _p(c["date"], s["date"]), width))
            story.append(Spacer(1, 2))

    def projects_section() -> None:
        if not cv["projects"]:
            return
        heading("Projects")
        for p in cv["projects"]:
            block: list[Any] = [_p(p["name"], s["role"])]
            if p["technologies"]:
                block.append(_p("Technologies: " + p["technologies"], s["sub"]))
            if p["description"]:
                block.append(_p(p["description"], s["body"]))
            bullets = [_p(b, s["bullet"], "\u2022") for b in p["bullets"]]
            story.append(KeepTogether(block + bullets[:1]))
            story.extend(bullets[1:])
            story.append(Spacer(1, 4))

    def languages_section() -> None:
        if cv["languages"]:
            heading("Languages")
            story.append(_p(", ".join(cv["languages"]), s["body"]))

    def achievements_section() -> None:
        if cv["achievements"]:
            heading("Achievements & Activities")
            story.extend(_p(a, s["bullet"], "\u2022") for a in cv["achievements"])

    # Experienced candidates lead with experience; freshers lead with education.
    if cv["experience"]:
        order = [summary_section, experience_section, education_section, skills_section,
                 certifications_section, projects_section, languages_section, achievements_section]
    else:
        order = [summary_section, education_section, projects_section, skills_section,
                 certifications_section, languages_section, achievements_section]
    for build_section in order:
        build_section()

    # ---- Document ----
    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawCentredString(doc.pagesize[0] / 2, 0.9 * cm, f"{cv['full_name']}  |  Page {doc.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=1.5 * cm,
        bottomMargin=1.6 * cm,
        title=f"{cv['full_name']} - CV" if cv["full_name"] else "CV",
        author=cv["full_name"] or "",
    )
    doc.build(story, onLaterPages=footer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Streamlit UI
# --------------------------------------------------------------------------- #

def get_setting(name: str, default: str = "") -> str:
    """Read from Streamlit secrets first, then environment variables."""
    try:
        value = st.secrets.get(name)
    except Exception:  # no secrets file present
        value = None
    return str(value) if value else os.environ.get(name, default)


def init_state() -> None:
    defaults = {
        "messages": [],       # [{"role", "content", "hidden"?}]
        "started": False,
        "interview_done": False,
        "cv_data": None,
        "pdf_bytes": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def render_sidebar() -> tuple[str, str, str, str, str]:
    """Returns (api_key, provider, model, accent_name, page_name)."""
    names = list(PROVIDERS)
    with st.sidebar:
        st.header("Settings")
        wanted = get_setting("LLM_PROVIDER", DEFAULT_PROVIDER)
        provider = st.selectbox(
            "AI provider",
            names,
            index=names.index(wanted) if wanted in names else 0,
            help="Groq (groq.com) and xAI Grok (x.ai) are different services with different API keys.",
        )
        cfg = PROVIDERS[provider]

        secret_key = get_setting(cfg["key_env"])
        if secret_key:
            st.success(f"{cfg['key_env']} loaded from secrets.")
            api_key = secret_key
        else:
            api_key = st.text_input(
                f"{provider} API key",
                type="password",
                help=f"Create one at {cfg['console']}. It is only kept in memory for this session.",
            )
        api_key = api_key.strip().strip("\"'").strip()

        # Auto-correct the provider when the key clearly belongs to the other one.
        for name, other in PROVIDERS.items():
            if name != provider and api_key.startswith(other["key_prefix"]):
                st.info(f"That looks like a {name} key, so the app is using {name}.")
                provider, cfg = name, other
                break
        if api_key and not api_key.startswith(cfg["key_prefix"]):
            st.warning(f"{provider} keys normally start with '{cfg['key_prefix']}'. Double-check what you pasted.")

        # Discover the models this key can actually use (cached per provider+key for the session).
        configured = get_setting(cfg["model_env"], cfg["default_model"])
        models: list[str] = []
        if api_key:
            cache = st.session_state.setdefault("models_cache", {})
            cache_key = f"{provider}|{api_key}"
            if cache_key not in cache:
                try:
                    cache[cache_key] = (list_chat_models(api_key, cfg["base_url"], cfg["model_prefix"]), "")
                except Exception as exc:
                    cache[cache_key] = ([], friendly_error(exc, provider))
            models, list_error = cache[cache_key]
            if list_error:
                st.error(list_error)
        if models:
            index = models.index(configured) if configured in models else 0
            model = st.selectbox(
                "Model", models, index=index, key=f"model_sel_{provider}",
                help=f"Models available to your {provider} key.",
            )
        else:
            model = st.text_input(
                "Model",
                value=configured,
                key=f"model_txt_{provider}",
                help=f"Could not load the model list; type a model name from {cfg['models_doc']}.",
            ).strip() or cfg["default_model"]
        st.divider()
        st.subheader("CV style")
        accent_name = st.selectbox("Accent colour", list(ACCENT_COLORS), index=0)
        page_name = st.selectbox("Page size", list(PAGE_SIZES), index=0)
        st.divider()
        st.caption(
            f"Your answers are sent to {provider} to write your CV. "
            "Nothing is stored by this app after you close the tab."
        )
    return api_key, provider, model, accent_name, page_name


def ask_agent(client: OpenAI, model: str) -> None:
    """Get the interviewer's next message and append it to the chat history."""
    api_messages = [{"role": "system", "content": INTERVIEW_SYSTEM_PROMPT}] + [
        {"role": m["role"], "content": m["content"]} for m in st.session_state.messages
    ]
    reply = call_llm(client, model, api_messages, temperature=0.5)
    if INTERVIEW_DONE_MARKER in reply:
        reply = reply.replace(INTERVIEW_DONE_MARKER, "").strip()
        st.session_state.interview_done = True
    st.session_state.messages.append({"role": "assistant", "content": reply or "Could you tell me more?"})


def main() -> None:
    st.set_page_config(page_title="AI CV Generator", page_icon="📄", layout="centered")
    init_state()
    api_key, provider, model, accent_name, page_name = render_sidebar()
    base_url = PROVIDERS[provider]["base_url"]

    st.title("📄 AI CV Generator")
    st.caption("Chat with an AI interviewer, then download a professional, job-ready PDF CV.")

    # ---- Step 1: photo ----
    st.subheader("1 · Profile photo (optional)")
    uploaded = st.file_uploader(
        "Upload a photo from your device",
        type=["png", "jpg", "jpeg"],
        help="Use a clear, professional head-and-shoulders photo. It will be cropped to a square.",
    )
    photo_bytes: bytes | None = None
    if uploaded is not None:
        try:
            photo_bytes = prepare_photo(uploaded.getvalue())
            st.image(photo_bytes, width=130, caption="How it will appear on your CV")
        except Exception:
            st.error("That image could not be read. Please upload a valid PNG or JPG file.")

    # ---- Step 2: interview ----
    st.subheader("2 · Interview")
    started = st.session_state.started
    if st.button("🔄 Restart conversation" if started else "🚀 Start conversation", key="start_btn"):
        if not api_key:
            st.warning(f"Please enter your {provider} API key in the sidebar first.")
        else:
            st.session_state.messages = [
                {"role": "user", "content": "Hi! I'd like to create a professional CV.", "hidden": True}
            ]
            st.session_state.update(started=True, interview_done=False, cv_data=None, pdf_bytes=None)
            try:
                with st.spinner("Starting the interview..."):
                    ask_agent(get_client(api_key, base_url), model)
            except Exception as exc:
                st.session_state.update(started=False, messages=[])
                st.error(friendly_error(exc, provider))

    if st.session_state.started:
        user_input = st.chat_input("Type your answer here...")
        if user_input and api_key:
            st.session_state.messages.append({"role": "user", "content": user_input})
            st.session_state.pdf_bytes = None  # CV is now out of date
            try:
                with st.spinner("Thinking..."):
                    ask_agent(get_client(api_key, base_url), model)
            except Exception as exc:
                st.error(friendly_error(exc, provider))
        elif user_input:
            st.warning(f"Please enter your {provider} API key in the sidebar.")

        for m in st.session_state.messages:
            if m.get("hidden"):
                continue
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

        if st.session_state.interview_done:
            st.success("Interview complete! Scroll down and click **Generate CV**.")
    else:
        st.info("Click **Start conversation** and the AI will ask for everything a great CV needs.")

    # ---- Step 3: generate ----
    st.subheader("3 · Generate your CV")
    answers = sum(1 for m in st.session_state.messages if m["role"] == "user" and not m.get("hidden"))
    ready = st.session_state.started and answers >= 2
    if not ready:
        st.caption("Answer a few of the interviewer's questions to unlock this button.")
    elif not st.session_state.interview_done:
        st.caption("Tip: the CV is best when the interview is complete, but you can generate at any time.")

    if st.button("✨ Generate CV", disabled=not ready, type="primary", key="gen_btn"):
        try:
            with st.spinner("Writing and formatting your CV..."):
                cv = extract_cv_data(get_client(api_key, base_url), model, st.session_state.messages)
                if not cv["full_name"]:
                    raise ValueError("I could not find your name yet. Please provide it in the chat and try again.")
                st.session_state.cv_data = cv
                st.session_state.pdf_bytes = build_cv_pdf(
                    cv, photo_bytes, ACCENT_COLORS[accent_name], PAGE_SIZES[page_name]
                )
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(friendly_error(exc, provider))

    if st.session_state.pdf_bytes:
        cv = st.session_state.cv_data
        st.success("Your CV is ready!")
        file_name = (re.sub(r"[^A-Za-z0-9]+", "_", cv["full_name"]).strip("_") or "CV") + "_CV.pdf"
        st.download_button(
            "⬇️ Download CV (PDF)",
            data=st.session_state.pdf_bytes,
            file_name=file_name,
            mime="application/pdf",
            key="download_btn",
        )
        with st.expander("Review the data used in your CV"):
            st.json(cv)
        st.caption("Missing or wrong something? Keep chatting above, then click **Generate CV** again.")


if __name__ == "__main__":
    main()
