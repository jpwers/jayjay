# Bytes Of Hope 

Bytes of Hope is a lightweight, production-ready disaster relief and emergency inventory management system designed to streamline crisis response operations. Developed with speed and reliability in mind, Bytes of Hope bridges the gap between field assessments, resource allocation, and real-time dispatch management under critical conditions.

## Features
- Inventory Monitoring in Real Time: Monitor the inventory of supplies, food supplies, medical kits, and equipment from all the central warehouses and relief centers.
- Emergency Identification: Identify the needs from the collected data from the supply assessment.
- Optimization of Dispatch and Route Planning: Plan and optimize the dispatch of the supplies and monitor the status of the dispatch to avoid any problems related to logistics.
- Operations Dashboard: Make use of an interactive dashboard with constant analysis to allow total control over the operations for the management.
- SQLite Persistence with No Downtime: Database configured with WAL (Write-Ahead Logging) mode (relief_data.db) to allow concurrent reading and writing of data.
- Report Downloads: Allows logging of the reports according to date. Reports downloadable in the formats of json, csv, or pdf.

## Screenshots

### Operations dashboard
![Operations dashboard](screenshots/Screenshot%202026-11-02%20191326.png)

![Operations dashboard in browser](screenshots/Screenshot%202026-11-02%20191311.png)

### Inventory
![Inventory page](screenshots/Screenshot%202026-11-02%20191402.png)

### Shelter dashboard
![Shelter dashboard](screenshots/Screenshot%202026-11-02%20191424.png)

### Supply contribution
![Supply contribution form](screenshots/Screenshot%202026-11-02%20191446.png)

### Supply assessment
![Supply assessment results](screenshots/Screenshot%202026-11-02%20191513.png)

### Request history and reports
![Request history and report export](screenshots/Screenshot%202026-11-02%20191349.png)

## Run locally

Requirements: Python 3. Install the app dependencies and start the Flask server:

```bash
cd \location\bytesofhope
python -m venv .venv.\.venv\scripts\activate.ps1
python -m pip install -r requirements.txt
python App.py
```

Open [http://127.0.0.1:5001] or [192.168.x.xxx:5001] in your browser. The app creates or updates its SQLite database at `Pp/FieldReady/relief_data.db` by default.

### Tech Stack
- Framework: Python 3.13 / Flask 3.1
- Server Interface: Gunicorn 23.0
- Database: SQLite3
- PDF Report Generation: ReportLab 4.x
- Frontend: HTML5, Modern CSS (styles.css), Jinja2 Templates
- Deployment: Render (render.yaml) / Heroku (Procfile)
## Deployment

See [Pp/FieldReady/DEPLOYING.md](Pp/FieldReady/DEPLOYING.md) for Render deployment instructions and production environment variables.

## Project files

- `Pp/FieldReady/App.py` — local development server entry point
- `Pp/FieldReady/web_app.py` — Flask routes, application logic, and SQLite setup
- `Pp/FieldReady/templates/` — Jinja templates
- `Pp/FieldReady/static/` — CSS and static assets
- `Pp/FieldReady/requirements.txt` — Python dependencies
