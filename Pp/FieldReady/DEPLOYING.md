# Bytes Of Hope deployment

## Local run

Install the dependencies and start the server:

```powershell
python -m pip install -r requirements.txt
python App.py
```

Open `http://127.0.0.1:5001`. For another device on the same Wi-Fi, open `http://<this-computer-LAN-IP>:5001` and allow Python through Windows Firewall on private networks. Do not forward port 5001 from your router for public internet use.

Local demo accounts are `admin` / `admin123`, `chennai_rep` / `chennai123`, and `mumbai_rep` / `mumbai123`.

New shelter or organization users can create accounts from the sign-in page. These accounts have shelter access; administrator access remains limited to the configured administrator account. Account credentials and shelter inventory are stored in the application database.

## Internet deployment with Render

1. Put `App.py`, `web_app.py`, `requirements.txt`, `Procfile`, `render.yaml`, `templates/`, and `static/` in a private GitHub repository. Do not commit `.env`, `relief_data.db`, or real credentials.
2. In Render, create a new Blueprint and connect that repository. Render reads `render.yaml`, builds the Flask app, provisions HTTPS, and mounts persistent storage for the SQLite database.
3. When prompted, enter unique strong values for `ADMIN_PASSWORD`, `CHENNAI_PASSWORD`, and `MUMBAI_PASSWORD`. Keep them in the hosting provider's secret settings; do not reuse the local demo passwords.
4. Open the HTTPS URL provided by Render. The database remains on the attached persistent disk across app restarts and deploys.

The persistent disk is required for SQLite data to survive deployments and may require a paid hosting plan. SQLite is configured for one app instance; use PostgreSQL before scaling to multiple instances.