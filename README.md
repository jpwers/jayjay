# Bytes Of Hope

Bytes Of Hope is a web app for coordinating emergency supplies between a central warehouse and shelters. Warehouse administrators can manage stock and dispatch requests; shelter representatives can record contributions, assess supply needs, and review request status.

## Features

- Dashboard with current inventory and request summaries
- Central and shelter inventory views
- Shelter contribution and warehouse restocking forms
- Needs assessments based on shelter population and on-hand stock
- Shared request queue and warehouse dispatch workflow
- SQLite persistence

## Run locally

Requirements: Python 3. Install the app dependencies and start the Flask server:

```bash
python -m pip install -r Pp/FieldReady/requirements.txt
cd Pp/FieldReady
python App.py
```

Open [http://127.0.0.1:5001](http://127.0.0.1:5001) in your browser. The app creates or updates its SQLite database at `Pp/FieldReady/relief_data.db` by default.

### Local demo accounts

| Role | Username | Password |
|---|---|---|
| Warehouse administrator | `admin` | `admin123` |
| Chennai shelter representative | `chennai_rep` | `chennai123` |
| Mumbai shelter representative | `mumbai_rep` | `mumbai123` |

These are local demo credentials only. Configure unique passwords and a secret key before deploying the app.

## Deployment

See [Pp/FieldReady/DEPLOYING.md](Pp/FieldReady/DEPLOYING.md) for Render deployment instructions and production environment variables.

## Project files

- `Pp/FieldReady/App.py` — local development server entry point
- `Pp/FieldReady/web_app.py` — Flask routes, application logic, and SQLite setup
- `Pp/FieldReady/templates/` — Jinja templates
- `Pp/FieldReady/static/` — CSS and static assets
- `Pp/FieldReady/requirements.txt` — Python dependencies
