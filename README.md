# Bytes Of Hope 

Bytes of Hope is a lightweight, production-ready disaster relief and emergency inventory management system designed to streamline crisis response operations. Developed with speed and reliability in mind, Bytes of Hope bridges the gap between field assessments, resource allocation, and real-time dispatch management under critical conditions.

## Features
- Inventory Monitoring in Real Time: Monitor the inventory of supplies, food supplies, medical kits, and equipment from all the central warehouses and relief centers.
- Emergency Identification: Identify the needs from the collected data from the supply assessment.
- Optimization of Dispatch and Route Planning: Plan and optimize the dispatch of the supplies and monitor the status of the dispatch to avoid any problems related to logistics.
- Operations Dashboard: Make use of an interactive dashboard with constant analysis to allow total control over the operations for the management.
- SQLite Persistence with No Downtime: Database configured with WAL (Write-Ahead Logging) mode (relief_data.db) to allow concurrent reading and writing of data.
- Report Downloads: Allows logging of the reports according to date. Reports downloadable in the formats of json, csv, or pdf.
- Priority-based request queue: Requests are ordered by urgency, with critical requests first, followed by high and moderate priority.

## Data models

The application stores its operational state as JSON in SQLite. Shelter records include a stable `shelter_id`, name, nullable priority, nullable latitude and longitude, and current stock. Request records include a stable `request_id`, the associated `shelter_id`, shelter name, item, quantity, urgency, status, and creation time.

On startup, existing shelter and request records are assigned IDs and given the new shelter fields without discarding existing data. The app does not currently collect shelter priority or coordinates, so those values remain unset.

## Screenshots

![Operations dashboard](screenshots/Screenshot%202026-11-02%20191326.png)

![Operations dashboard in browser](screenshots/Screenshot%202026-11-02%20191311.png)

![Inventory page](screenshots/Screenshot%202026-11-02%20191402.png)

![Shelter dashboard](screenshots/Screenshot%202026-11-02%20191424.png)

![Supply contribution form](screenshots/Screenshot%202026-11-02%20191446.png)

![Request history and report export](screenshots/Screenshot%202026-11-02%20191349.png)

## Run locally

Requirements: Python 3. Install the app dependencies and start the Flask server:

```bash
cd bytesofhope
python -m pip install -r requirements.txt
python App.py
```

Open [http://127.0.0.1:5001](http://127.0.0.1:5001) in your browser. The app creates or updates its SQLite database at `bytesofhope/relief_data.db` by default.

### Tech Stack
- Framework: Python 3.13 / Flask 3.1
- Server Interface: Gunicorn 23.0
- Database: SQLite3
- PDF Report Generation: ReportLab 4.x
- Frontend: HTML5, Modern CSS (styles.css), Jinja2 Templates
- Deployment: Render (render.yaml) / Heroku (Procfile)
## Deployment

See [bytesofhope/DEPLOYING.md](bytesofhope/DEPLOYING.md) for Render deployment instructions and production environment variables.

## Project files

- `bytesofhope/App.py` — local development server entry point
- `bytesofhope/web_app.py` — Flask routes, application logic, and SQLite setup
- `bytesofhope/templates/` — Jinja templates
- `bytesofhope/static/` — CSS and static assets
- `bytesofhope/requirements.txt` — Python dependencies
