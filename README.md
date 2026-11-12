# Site Survey

Enter a website address and get its **pages, services, purpose and contact info**.

- Frontend: HTML, CSS, JavaScript (`static/`)
- Backend: Python + Flask (`app.py`, `scraper.py`)

## Run in VS Code

1. Open this folder in VS Code (File > Open Folder).
2. Open a terminal (Ctrl+`) and create a virtual environment:
   - Windows: `python -m venv .venv` then `.venv\Scripts\activate`
   - macOS/Linux: `python3 -m venv .venv` then `source .venv/bin/activate`
3. Install packages: `pip install -r requirements.txt`
4. Start the server: `python app.py` (or press F5 and pick "Run Site Survey").
5. Open http://127.0.0.1:5000 in your browser, enter a website and click **Scan site**.

If VS Code asks for a Python interpreter, choose the one inside `.venv`.

## How it works

1. The homepage is downloaded and its internal links are collected and grouped (about, services, contact, other).
2. Contact, about and services pages are read first, up to the "Pages to read" limit.
3. Each page is searched for:
   - **Purpose**: meta description, structured data, or the first meaningful paragraph.
   - **Services**: headings such as "Services" or "What we do" and the lists under them, plus service links.
   - **Contact**: `mailto:` and `tel:` links, emails and phone numbers in the text, `<address>` tags, structured data and social links.

## Limits

- Pages that build their content with JavaScript will look empty. Those need a browser tool such as Playwright.
- Services and purpose are found by pattern matching, so results are good guesses, not guarantees.
- The tool follows robots.txt and blocks private network addresses. Only scan sites you are allowed to read.
