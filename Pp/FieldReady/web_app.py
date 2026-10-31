import difflib
import json
import os
import re
import secrets
import sqlite3
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "relief_data.db"))
IS_PRODUCTION = os.environ.get("APP_ENV", "development").lower() == "production"

if IS_PRODUCTION:
    required_secrets = ("SECRET_KEY", "ADMIN_PASSWORD", "CHENNAI_PASSWORD", "MUMBAI_PASSWORD")
    missing_secrets = [name for name in required_secrets if not os.environ.get(name)]
    if missing_secrets:
        raise RuntimeError(f"Set these production environment variables: {', '.join(missing_secrets)}")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    MAX_CONTENT_LENGTH=1_000_000,
)

ACCOUNTS = {
    "admin": {
        "role": "admin",
        "password": os.environ.get("ADMIN_PASSWORD", "admin123"),
        "name": "Warehouse administrator",
    },
    "chennai_rep": {
        "role": "shelter",
        "password": os.environ.get("CHENNAI_PASSWORD", "chennai123"),
        "name": "Chennai Central Shelter",
    },
    "mumbai_rep": {
        "role": "shelter",
        "password": os.environ.get("MUMBAI_PASSWORD", "mumbai123"),
        "name": "Mumbai Coastal Safehouse",
    },
}


def default_state():
    return {
        "central_inventory": {
            "Water": 500,
            "Food": 200,
            "First Aid Kits": 50,
            "Bedding": 100,
            "Toiletries": 150,
        },
        "shelters": {
            "chennai_rep": {
                "name": "Chennai Central Shelter",
                "current_stock": {"Water": 30, "Food": 10, "First Aid Kits": 2, "Bedding": 5, "Toiletries": 10},
            },
            "mumbai_rep": {
                "name": "Mumbai Coastal Safehouse",
                "current_stock": {"Water": 100, "Food": 50, "First Aid Kits": 10, "Bedding": 20, "Toiletries": 30},
            },
        },
        "requests": [],
        "last_requests": {},
    }


def valid_stock(stock):
    return {
        item: quantity
        for item, quantity in stock.items()
        if isinstance(item, str) and isinstance(quantity, int) and quantity >= 0
    }


def migrate_json_state(state):
    legacy_path = BASE_DIR / "relief_data.json"
    if not legacy_path.exists():
        return state
    try:
        saved = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state

    state["central_inventory"].update(valid_stock(saved.get("central_inventory", {})))
    for username, stock in saved.get("shelter_inventory", {}).items():
        if username in state["shelters"] and isinstance(stock, dict):
            state["shelters"][username]["current_stock"].update(valid_stock(stock))
    state["requests"] = saved.get("requests_queue", []) if isinstance(saved.get("requests_queue"), list) else []
    state["last_requests"] = saved.get("last_requests", {}) if isinstance(saved.get("last_requests"), dict) else {}
    return state


def connect_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with connect_database() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS app_state (id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS user_accounts ("
            "username TEXT PRIMARY KEY COLLATE NOCASE, "
            "password_hash TEXT NOT NULL, "
            "display_name TEXT NOT NULL)"
        )
        saved = connection.execute("SELECT payload FROM app_state WHERE id = 1").fetchone()
        if saved is None:
            state = migrate_json_state(default_state())
            connection.execute("INSERT INTO app_state (id, payload) VALUES (1, ?)", (json.dumps(state),))
        else:
            state = json.loads(saved["payload"])
            if not isinstance(state.get("last_requests"), dict):
                last_assessments = state.get("last_assessments", {})
                state["last_requests"] = last_assessments if isinstance(last_assessments, dict) else {}
                state.pop("last_assessments", None)
                connection.execute(
                    "UPDATE app_state SET payload = ? WHERE id = 1",
                    (json.dumps(state),),
                )


initialize_database()


@app.before_request
def prepare_request():
    if request.method == "POST":
        token = session.get("csrf_token", "")
        submitted = request.form.get("csrf_token", "")
        if not token or not secrets.compare_digest(token, submitted):
            abort(400, "Invalid form token. Reload the page and try again.")

    if request.endpoint == "static":
        return

    g.db = connect_database()
    if request.method == "POST":
        g.db.execute("BEGIN IMMEDIATE")
    row = g.db.execute("SELECT payload FROM app_state WHERE id = 1").fetchone()
    g.state = json.loads(row["payload"])


@app.after_request
def persist_request(response):
    connection = g.get("db")
    if connection is None:
        return response
    try:
        if request.method == "POST" and response.status_code < 400:
            connection.execute("UPDATE app_state SET payload = ? WHERE id = 1", (json.dumps(g.state),))
            connection.commit()
        elif request.method == "POST":
            connection.rollback()
    finally:
        connection.close()
    return response


@app.teardown_request
def close_database(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


@app.context_processor
def template_helpers():
    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return session["csrf_token"]

    def current_display_name():
        if session.get("display_name"):
            return session["display_name"]
        account = ACCOUNTS.get(session.get("username"), {})
        return account.get("name", "")

    return {"csrf_token": csrf_token, "current_display_name": current_display_name}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def role_required(role):
    def decorate(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get("role") != role:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorate


def current_state():
    return g.state


def current_shelter_key():
    return session["username"]


def normalize_web_item_name(raw_name, inventory):
    cleaned = raw_name.strip().lower()
    if any(word in cleaned for word in ("water", "drink", "watr", "bottle", "fluid", "hydration")):
        return "Water"
    if any(word in cleaned for word in ("food", "meal", "ration", "snack", "eat")):
        return "Food Kits"
    if any(word in cleaned for word in ("first aid", "medical", "medicine", "aid", "kt", "kits")):
        return "First Aid Kits"
    if any(word in cleaned for word in ("bed", "blanket", "pillow", "bedng", "cot")):
        return "Bedding"
    if any(word in cleaned for word in ("toilet", "soap", "hygiene", "sanitizer", "tissues")):
        return "Toiletries"
    matches = difflib.get_close_matches(cleaned, [item.lower() for item in inventory], n=1, cutoff=0.4)
    if matches:
        return next(item for item in inventory if item.lower() == matches[0])
    return raw_name.strip().title()


def parse_positive_integer(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

import difflib
import json
import os
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox
    from tkinter import ttk
except ImportError:
    tk = None
    messagebox = None
    ttk = None

central_inventory = {
    "Water": 500,
    "Food": 200,
    "First Aid ": 50,
    "Bedding": 100,
    "Toiletries": 150
}

per_capita_ratios = {
    "Water": lambda total, injured: total * 2,
    "Food": lambda total, injured: total * 1,
    "First Aid ": lambda total, injured: injured * 1,
    "Bedding": lambda total, injured: total * 1,
    "Toiletries": lambda total, injured: total * 1
}

shelters_accounts = {
    "chennai_rep": {
        "password": "chennai123", 
        "shelter_name": "Chennai Central Shelter",
        "current_stock": {"Water": 30, "Food": 10, "First Aid": 2, "Bedding": 5, "Toiletries": 10}
    },
    "mumbai_rep": {
        "password": "mumbai123", 
        "shelter_name": "Mumbai Coastal Safehouse",
        "current_stock": {"Water": 100, "Food": 50, "First Aid": 10, "Bedding": 20, "Toiletries": 30}
    }
}

requests_queue = []
urgency_rank = {"Critical": 1, "High": 2, "Moderate": 3}
last_requests = {}
STATE_FILE = Path(__file__).with_name("relief_data.json")


def save_state(path=STATE_FILE):
    state = {
        "central_inventory": central_inventory,
        "shelter_inventory": {
            username: account["current_stock"]
            for username, account in shelters_accounts.items()
        },
        "requests_queue": requests_queue,
        "last_requests": last_requests,
    }
    temporary_path = Path(f"{path}.tmp")
    try:
        temporary_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(temporary_path, path)
        return True
    except OSError as error:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        messagebox.showerror("Save failed", f"Could not save application data:\n{error}")
        return False


def load_state(path=STATE_FILE):
    global requests_queue
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        return f"Saved data could not be loaded. Default inventory will be used.\n{error}"

    def restore_stock(saved, current):
        if not isinstance(saved, dict):
            return
        for item, quantity in saved.items():
            if isinstance(item, str) and isinstance(quantity, int) and not isinstance(quantity, bool) and quantity >= 0:
                current[item] = quantity
                per_capita_ratios.setdefault(item, lambda total, injured: total)

    restore_stock(state.get("central_inventory"), central_inventory)
    saved_shelters = state.get("shelter_inventory", {})
    if isinstance(saved_shelters, dict):
        for username, stock in saved_shelters.items():
            account = shelters_accounts.get(username)
            if account:
                restore_stock(stock, account["current_stock"])

    saved_requests = state.get("requests_queue", [])
    if isinstance(saved_requests, list):
        requests_queue = [
            request for request in saved_requests
            if isinstance(request, dict)
            and isinstance(request.get("shelter"), str)
            and isinstance(request.get("item"), str)
            and isinstance(request.get("qty"), int)
            and not isinstance(request.get("qty"), bool)
            and request["qty"] > 0
            and request.get("urgency") in urgency_rank
            and isinstance(request.get("status"), str)
        ]

    saved_requests = state.get("last_requests", {})
    if isinstance(saved_requests, dict):
        for username, assessment in saved_requests.items():
            if username not in shelters_accounts or not isinstance(assessment, dict):
                continue
            people = assessment.get("people")
            injured = assessment.get("injured")
            if (isinstance(people, int) and not isinstance(people, bool) and people > 0
                    and isinstance(injured, int) and not isinstance(injured, bool)
                    and 0 <= injured <= people):
                last_requests[username] = {"people": people, "injured": injured}
    return None

def normalize_item_name(raw_name):
    """
    Combines keyword mapping and Python's difflib fuzzy matching 
    to automatically correct spelling errors and prevent duplicate items.
    """
    cleaned = raw_name.strip().lower()
    
    # 1. Check keyword synonyms first
    if any(k in cleaned for k in ["water", "drink", "watr", "bottle", "fluid", "hydration"]):
        return "Water"
    if any(k in cleaned for k in ["food", "meal", "ration", "snack", "eat"]):
        return "Food Kits"
    if any(k in cleaned for k in ["first aid", "medical", "medicine", "aid", "kt", "kits"]):
        return "First Aid Kits"
    if any(k in cleaned for k in ["bed", "blanket", "pillow", "bedng", "cot"]):
        return "Bedding"
    if any(k in cleaned for k in ["toilet", "soap", "hygiene", "sanitizer", "tissues"]):
        return "Toiletries"
    
    item_lower_map = {item.lower(): item for item in central_inventory.keys()}
    matches = difflib.get_close_matches(cleaned, item_lower_map.keys(), n=1, cutoff=0.4)
    
    if matches:
        corrected = item_lower_map[matches[0]]
        print(f"[Typo Auto-Correct]: '{raw_name}' was corrected to '{corrected}'")
        return corrected
    
    return raw_name.strip().title()

def warehouse_admin_menu():
    while True:
        print("\n==========================================")
        print("WAREHOUSE ADMIN PORTAL")
        print("==========================================")
        print("1. View Central Stockpile Levels")
        print("2. Restock Central Inventory (Bulk & Smart Match)")
        print("3. Run Priority Dispatch Matcher")
        print("4. Log Out")
        
        choice = input("\nSelect an option (1-4): ")
        
        if choice == '1':
            print("\nCentral Warehouse Stock:")
            for item, qty in central_inventory.items():
                print(f" - {item}: {qty} units available")
                
        elif choice == '2':
            print("\n--- Bulk Restock & Smart Item Addition ---")
            print("Enter items to add. Type 'done' when finished.")
            while True:
                raw_input_name = input("Enter item name (e.g., 'watr', 'meals') or 'done': ").strip()
                if raw_input_name.lower() == 'done':
                    break
                
                item_name = normalize_item_name(raw_input_name)
                
                try:
                    add_qty = int(input(f"Enter quantity for {item_name}: "))
                    
                    if item_name in central_inventory:
                        central_inventory[item_name] += add_qty
                    else:
                        central_inventory[item_name] = add_qty
                        if item_name not in per_capita_ratios:
                            per_capita_ratios[item_name] = lambda total, injured: total * 1
                        print(f"New commodity '{item_name}' registered in system!")
                        
                    print(f"Success! Total stock for {item_name}: {central_inventory[item_name]} units.")
                except ValueError:
                    print("Please enter a valid number for quantity.")
                
        elif choice == '3':
            run_priority_matcher()
            
        elif choice == '4':
            print("Logging out of Admin Portal...")
            break
        else:
            print("Invalid option.")

def shelter_representative_portal(username):
    shelter_data = shelters_accounts[username]
    shelter_name = shelter_data["shelter_name"]
    
    while True:
        print(f"\n==========================================")
        print(f"{shelter_name.upper()} PORTAL")
        print(f"==========================================")
        print("1. View Central Warehouse Stock")
        print("2. View My Shelter's Current On-Hand Inventory")
        print("3. Add External Contributions (Bulk & Auto-Correct)")
        print("4. Calculate Per-Capita Needs & Handle Surplus/Deficit")
        print("5. Log Out")
        
        choice = input("\nSelect an option (1-5): ")
        
        if choice == '1':
            print("\nCentral Warehouse Stock:")
            for item, qty in central_inventory.items():
                print(f" - {item}: {qty} units available")
                
        elif choice == '2':
            print(f"\n{shelter_name} Current Inventory:")
            for item, qty in shelter_data["current_stock"].items():
                print(f" - {item}: {qty} units on-hand")
                
        elif choice == '3':
            print(f"\n--- Log Multiple Outside Contributions ---")
            print("Enter items received from donors. Type 'done' when finished.")
            while True:
                raw_input_name = input("Enter item name (e.g., 'watr', 'first aid kts') or 'done': ").strip()
                if raw_input_name.lower() == 'done':
                    break
                
                item = normalize_item_name(raw_input_name)
                
                try:
                    qty = int(input(f"Enter Quantity for {item}: "))
                    if item in shelter_data["current_stock"]:
                        shelter_data["current_stock"][item] += qty
                    else:
                        shelter_data["current_stock"][item] = qty
                        if item not in central_inventory:
                            central_inventory[item] = 0
                            per_capita_ratios[item] = lambda total, injured: total * 1
                    print(f"Added {qty} units of {item} to your shelter stockpile!")
                except ValueError:
                    print("Please enter a valid number for quantity.")
                
        elif choice == '4':
            process_shelter_needs_and_surplus(username)
            
        elif choice == '5':
            print(f"Logging out of {shelter_name} Portal...")
            break
        else:
            print("Invalid option.")

def process_shelter_needs_and_surplus(username):
    shelter_data = shelters_accounts[username]
    shelter_name = shelter_data["shelter_name"]
    
    print(f"\n--- {shelter_name} PER-CAPITA & SURPLUS EVALUATION ---")
    
    try:
        total_people = int(input("Enter total population currently at your shelter: "))
        injured_people = int(input("Enter number of injured people requiring medical care: "))
    except ValueError:
        print("Please enter valid numbers.")
        return

    required_needs = {}
    for item, formula in per_capita_ratios.items():
        required_needs[item] = formula(total_people, injured_people)

    injury_ratio = injured_people / total_people if total_people > 0 else 0
    if injury_ratio > 0.25 or total_people > 150:
        urgency = "Critical"
    elif injury_ratio > 0.10:
        urgency = "High"
    else:
        urgency = "Moderate"

    print(f"\n[ASSESSMENT RESULTS FOR {shelter_name}]")
    print(f" • Assessed Urgency: {urgency} (Injury Rate: {injury_ratio:.1%})")
    print(f" • Gross Calculated Per-Capita Needs:")
    for item, req_qty in required_needs.items():
        print(f"   - {item}: {req_qty} units needed")

    print("\n--- ON-HAND STOCK, DEFICIT & SURPLUS CHECK ---")
    net_requests = {}
    surplus_donations = {}

    for item, req_qty in required_needs.items():
        on_hand = shelter_data["current_stock"].get(item, 0)
        print(f"\nItem: {item} | Required: {req_qty} | On-Hand: {on_hand}")
        
        if on_hand >= req_qty:
            surplus = on_hand - req_qty
            print(f" SURPLUS! Excess of {surplus} units.")
            if surplus > 0:
                donate = input(f"Would you like to donate the {surplus} surplus units back to central warehouse? (y/n): ")
                if donate.lower() == 'y':
                    surplus_donations[item] = surplus
        else:
            deficit = req_qty - on_hand
            net_requests[item] = deficit
            print(f" DEFICIT! Short by {deficit} units.")

    confirm = input("\nSubmit net deficit requests and donate surpluses? (y/n): ")
    if confirm.lower() == 'y':
        for item, deficit_qty in net_requests.items():
            requests_queue.append({
                "shelter": shelter_name,
                "item": item,
                "qty": deficit_qty,
                "urgency": urgency,
                "status": "Pending"
            })
            
        for item, surplus_qty in surplus_donations.items():
            if item not in central_inventory:
                central_inventory[item] = 0
            central_inventory[item] += surplus_qty
            shelter_data["current_stock"][item] -= surplus_qty
            print(f"Transferred {surplus_qty} surplus units of {item} back to Central Warehouse!")
            
        print("Operations successfully recorded!")
    else:
        print("Submission cancelled.")

def run_priority_matcher():
    if not requests_queue:
        print("\nNo pending deficit requests in queue.")
        return

    print("\n--- ⚡ EXECUTING PRIORITY DISPATCH ---")
    pending = [r for r in requests_queue if r['status'] == 'Pending']
    sorted_requests = sorted(pending, key=lambda r: urgency_rank[r['urgency']])

    for req in sorted_requests:
        shelter = req['shelter']
        item = req['item']
        needed = req['qty']
        urgency = req['urgency']
        
        print(f"\nProcessing Deficit [{shelter}] - Need {needed}x {item} (Urgency: {urgency})")
        
        if item in central_inventory:
            available = central_inventory[item]
            if available >= needed:
                central_inventory[item] -= needed
                req['status'] = 'Fulfilled'
                print(f" -> FULFILLED: Dispatched {needed} units. Central stock left: {central_inventory[item]}")
            elif available > 0:
                req['status'] = f'Partially Fulfilled ({available} sent)'
                central_inventory[item] = 0
                print(f" -> PARTIAL: Dispatched remaining {available} units. Central stock is now 0.")
            else:
                req['status'] = 'Unfulfilled (Out of Stock)'
                print(f" -> FAILED: Central warehouse out of stock for {item}.")
        else:
            req['status'] = 'Unfulfilled (Item Not Found)'

class ReliefApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bytes Of Hope | By Muroor Bytes")
        self.root.geometry("1180x760")
        self.root.minsize(900, 620)
        self.root.configure(bg="#F3F5F1")
        self.user = None
        self.role = None
        self.assessment = None
        
        load_state()
        
        self.configure_styles()
        self.show_login()

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground="#26332D", rowheight=34, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background="#E9EEE8", foreground="#425349", font=("Segoe UI Semibold", 9), padding=(10, 9), relief="flat")
        style.map("Treeview", background=[("selected", "#D9E9DD")], foreground=[("selected", "#173E2A")])
        style.configure("TCombobox", padding=8, fieldbackground="#FFFFFF", background="#FFFFFF")
        style.configure("TEntry", padding=8)
        style.configure("Primary.TButton", background="#176B45", foreground="#FFFFFF", padding=(15, 10), font=("Segoe UI Semibold", 10), borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#105638")], foreground=[("active", "#FFFFFF")])
        style.configure("Quiet.TButton", background="#E9EEE8", foreground="#2E4638", padding=(13, 9), font=("Segoe UI Semibold", 9), borderwidth=0)
        style.map("Quiet.TButton", background=[("active", "#DCE5DC")])

    def clear(self):
        for child in self.root.winfo_children():
            child.destroy()

    def show_login(self):
        self.clear()
        page = tk.Frame(self.root, bg="#F3F5F1")
        page.pack(fill="both", expand=True)
        brand = tk.Frame(page, bg="#174F37", width=390)
        brand.pack(side="left", fill="y")
        brand.pack_propagate(False)
        tk.Label(brand, text="BYTES OF\nHOPE", justify="left", bg="#174F37", fg="#FFFFFF", font=("Segoe UI Semibold", 32), anchor="w").pack(anchor="w", padx=48, pady=(90, 18))
        tk.Label(brand, text="By Muroor Bytes", justify="left", bg="#174F37", fg="#83EFB2", font=("Segoe UI", 11), anchor="w").pack(anchor="w", padx=48, pady=(0, 18))
        tk.Frame(brand, bg="#D8A64C", height=4, width=54).pack(anchor="w", padx=48, pady=(0, 25))
        tk.Label(brand, text="Emergency supply\ncoordination network", justify="left", bg="#174F37", fg="#D5E4D9", font=("Segoe UI", 15), anchor="w").pack(anchor="w", padx=48)
        tk.Label(brand, text="WAREHOUSE  /  SHELTER  /  RESPONSE", bg="#174F37", fg="#AFC8B7", font=("Segoe UI Semibold", 8)).pack(side="bottom", anchor="w", padx=48, pady=35)

        form = tk.Frame(page, bg="#F3F5F1")
        form.pack(side="left", fill="both", expand=True)
        panel = tk.Frame(form, bg="#F3F5F1", width=390)
        panel.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(panel, text="Operations sign-in", bg="#F3F5F1", fg="#1F3027", font=("Segoe UI Semibold", 24)).pack(anchor="w")
        tk.Label(panel, text="Choose your role to access the supply network.", bg="#F3F5F1", fg="#66756A", font=("Segoe UI", 10)).pack(anchor="w", pady=(7, 28))
        tk.Label(panel, text="ACCOUNT TYPE", bg="#F3F5F1", fg="#526358", font=("Segoe UI Semibold", 8)).pack(anchor="w", pady=(0, 7))
        self.login_role = ttk.Combobox(panel, state="readonly", values=["Warehouse administrator", "Shelter representative"])
        self.login_role.current(0)
        self.login_role.pack(fill="x", pady=(0, 16))
        self.login_user_label = tk.Label(panel, text="USERNAME", bg="#F3F5F1", fg="#526358", font=("Segoe UI Semibold", 8))
        self.login_user_label.pack(anchor="w", pady=(0, 7))
        self.login_user = ttk.Entry(panel)
        self.login_user.insert(0, "admin")
        self.login_user.pack(fill="x", pady=(0, 16))
        tk.Label(panel, text="PASSWORD", bg="#F3F5F1", fg="#526358", font=("Segoe UI Semibold", 8)).pack(anchor="w", pady=(0, 7))
        self.login_password = ttk.Entry(panel, show="*")
        self.login_password.pack(fill="x", pady=(0, 23))
        ttk.Button(panel, text="Sign in", style="Primary.TButton", command=self.login).pack(fill="x")
        self.login_role.bind("<<ComboboxSelected>>", self.update_login_fields)
        self.login_password.bind("<Return>", lambda _event: self.login())

    def update_login_fields(self, _event=None):
        is_admin = self.login_role.get() == "Warehouse administrator"
        self.login_user_label.config(text="USERNAME")
        self.login_user.delete(0, "end")
        self.login_user.insert(0, "admin" if is_admin else "chennai_rep")

    def login(self):
        is_admin = self.login_role.get() == "Warehouse administrator"
        username = self.login_user.get().strip()
        password = self.login_password.get()
        if is_admin:
            if username != "admin" or password != "admin123":
                messagebox.showerror("Sign-in failed", "Check the administrator username and password.")
                return
            self.role = "admin"
            self.user = "admin"
        else:
            account = shelters_accounts.get(username)
            if not account or account["password"] != password:
                messagebox.showerror("Sign-in failed", "Check the shelter username and password.")
                return
            self.role = "shelter"
            self.user = username
        self.show_shell()

    def show_shell(self):
        self.clear()
        shell = tk.Frame(self.root, bg="#F3F5F1")
        shell.pack(fill="both", expand=True)
        top = tk.Frame(shell, bg="#FFFFFF", height=66)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="Bytes Of Hope", bg="#FFFFFF", fg="#176B45", font=("Segoe UI Semibold", 14)).pack(side="left", padx=(25, 10))
        tk.Label(top, text="By Muroor Bytes", bg="#FFFFFF", fg="#87938A", font=("Segoe UI Semibold", 8)).pack(side="left", pady=(3, 0))
        identity = "Warehouse administrator" if self.role == "admin" else shelters_accounts[self.user]["shelter_name"]
        tk.Label(top, text=identity, bg="#FFFFFF", fg="#435248", font=("Segoe UI", 9)).pack(side="right", padx=(8, 24))
        ttk.Button(top, text="Log out", style="Quiet.TButton", command=self.show_login).pack(side="right", pady=12)
        tk.Frame(shell, bg="#DDE4DC", height=1).pack(fill="x")

        middle = tk.Frame(shell, bg="#F3F5F1")
        middle.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(middle, bg="#FFFFFF", width=215)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Label(self.sidebar, text="WORKSPACE", bg="#FFFFFF", fg="#97A198", font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=22, pady=(27, 13))
        self.content = tk.Frame(middle, bg="#F3F5F1")
        self.content.pack(side="left", fill="both", expand=True)
        
        pages = [("Overview", "Overview"), ("Inventory", "Inventory"), ("Needs & requests", "Requests")]
        if self.role == "admin":
            pages += [("Restock warehouse", "Restock"), ("Dispatch queue", "Dispatch")]
        else:
            pages += [("Shelter contribution", "Contribution"), ("Assess shelter needs", "Assessment")]
            
        for label, page_name in pages:
            ttk.Button(self.sidebar, text=label, style="Quiet.TButton", command=lambda name=page_name: self.show_page(name)).pack(fill="x", padx=13, pady=3)
        self.show_page("Overview")

    def page_heading(self, title, subtitle):
        header = tk.Frame(self.content, bg="#F3F5F1")
        header.pack(fill="x", padx=32, pady=(28, 20))
        tk.Label(header, text=title, bg="#F3F5F1", fg="#1E3026", font=("Segoe UI Semibold", 23)).pack(anchor="w")
        tk.Label(header, text=subtitle, bg="#F3F5F1", fg="#6B786E", font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))

    def show_page(self, page_name):
        for child in self.content.winfo_children():
            child.destroy()
        handlers = {
            "Overview": self.overview_page,
            "Inventory": self.inventory_page,
            "Requests": self.requests_page,
            "Restock": self.restock_page,
            "Contribution": self.contribution_page,
            "Assessment": self.assessment_page,
            "Dispatch": self.dispatch_page,
        }
        handlers[page_name]()

    def panel(self, parent, title=None):
        frame = tk.Frame(parent, bg="#FFFFFF", highlightbackground="#E1E7E0", highlightthickness=1)
        if title:
            tk.Label(frame, text=title, bg="#FFFFFF", fg="#34453A", font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=17, pady=(15, 10))
        return frame

    def make_table(self, parent, columns, height=8):
        table = ttk.Treeview(parent, columns=columns, show="headings", height=height)
        for column, label, width in columns:
            table.heading(column, text=label)
            table.column(column, width=width, minwidth=65, anchor="w", stretch=True)
        table.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        return table

    def overview_page(self):
        shelter = shelters_accounts[self.user] if self.role == "shelter" else None
        name = shelter["shelter_name"] if shelter else "Operation Hub"
        self.page_heading(name, "Live view of essential supplies and response activity.")
        pending = [request for request in requests_queue if request_is_open(request)]
        critical = sum(request["urgency"] == "Critical" for request in pending)
        if shelter:
            stock = shelter["current_stock"]
            metrics = [
                ("ON-HAND UNITS", sum(stock.values()), "Across tracked supplies"), 
                ("ITEM CATEGORIES", len(stock), "In your shelter stock"), 
                ("OPEN REQUESTS", sum(request["shelter"] == name for request in pending), "Awaiting warehouse action")
            ]
        else:
            metrics = [
                ("CENTRAL UNITS", sum(central_inventory.values()), "Available in warehouse"), 
                ("PENDING REQUESTS", len(pending), "Across all shelters"), 
                ("CRITICAL", critical, "Requests needing attention")
            ]
        cards = tk.Frame(self.content, bg="#F3F5F1")
        cards.pack(fill="x", padx=32)
        for title, value, detail in metrics:
            card = self.panel(cards)
            card.pack(side="left", fill="both", expand=True, padx=(0, 12))
            tk.Label(card, text=title, bg="#FFFFFF", fg="#78867C", font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=16, pady=(14, 3))
            tk.Label(card, text=f"{value:,}", bg="#FFFFFF", fg="#1F3D2D", font=("Segoe UI Semibold", 25)).pack(anchor="w", padx=16)
            tk.Label(card, text=detail, bg="#FFFFFF", fg="#7C887F", font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(2, 14))
            
        section = "Your recent requests" if shelter else "Latest network requests"
        block = self.panel(self.content, section)
        block.pack(fill="both", expand=True, padx=32, pady=20)
        table = self.make_table(block, [("shelter", "SHELTER", 230), ("item", "SUPPLY", 150), ("qty", "QTY", 70), ("urgency", "PRIORITY", 110), ("status", "STATUS", 210)], 9)
        items = [request for request in requests_queue if not shelter or request["shelter"] == name]
        for request in reversed(items[-30:]):
            table.insert("", "end", values=(request["shelter"], request["item"], request["qty"], request["urgency"], request["status"]))

    def inventory_page(self):
        shelter = shelters_accounts[self.user] if self.role == "shelter" else None
        self.page_heading("Inventory", "Compare available supplies and current stock levels.")
        block = self.panel(self.content, "Availaible Stock")
        block.pack(fill="both", expand=True, padx=32, pady=(0, 24))
        table = self.make_table(block, [("item", "SUPPLY", 220), ("central", "CENTRAL WAREHOUSE", 180), ("shelter", "SHELTER ON-HAND", 180), ("position", "YOUR POSITION", 170)], 13)
        for item in central_inventory:
            central = central_inventory.get(item, 0)
            on_hand = shelter["current_stock"].get(item, 0) if shelter else None
            position = f"{on_hand} on hand" if shelter and on_hand else ("Not stocked" if shelter else "Network")
            table.insert("", "end", values=(item, f"{central:,} units", f"{on_hand:,} units" if shelter and on_hand is not None else "-", position))

    def entry_form(self, parent, fields):
        values = {}
        for label, key, default in fields:
            tk.Label(parent, text=label.upper(), bg="#FFFFFF", fg="#58675D", font=("Segoe UI Semibold", 8)).pack(anchor="w", pady=(12, 5))
            entry = ttk.Entry(parent)
            if default:
                entry.insert(0, default)
            entry.pack(fill="x")
            values[key] = entry
        return values

    def restock_page(self):
        self.page_heading("Stock UP", "Record deliveries and stock up centeral stock.")
        block = self.panel(self.content, "Add stock")
        block.pack(fill="x", padx=32, pady=(0, 18))
        form = tk.Frame(block, bg="#FFFFFF")
        form.pack(fill="x", padx=18, pady=(0, 18))
        values = self.entry_form(form, [("Commodity Name", "item", ""), ("Quantity", "qty", "")])
        ttk.Button(form, text="Add to warehouse", style="Primary.TButton", command=lambda: self.add_stock(values, False)).pack(anchor="w", pady=(18, 0))
        self.stock_summary(block)

    def contribution_page(self):
        self.page_heading("Shelter contribution", "Add donated supplies to your shelter's on-hand inventory.")
        block = self.panel(self.content, "Record a delivery")
        block.pack(fill="x", padx=32, pady=(0, 18))
        form = tk.Frame(block, bg="#FFFFFF")
        form.pack(fill="x", padx=18, pady=(0, 18))
        values = self.entry_form(form, [("Commodity Name", "item", ""), ("Quantity", "qty", "")])
        ttk.Button(form, text="Add contribution", style="Primary.TButton", command=lambda: self.add_stock(values, True)).pack(anchor="w", pady=(18, 0))
        tk.Label(block, text="Unrecognized supply names are added as new categories.", bg="#FFFFFF", fg="#7B887F", font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=(0, 16))

    def stock_summary(self, parent):
        table = self.make_table(parent, [("item", "SUPPLY", 250), ("qty", "AVAILABLE", 160)], 7)
        for item, qty in central_inventory.items():
            table.insert("", "end", values=(item, f"{qty:,} units"))

    def add_stock(self, values, to_shelter):
        raw_item = values["item"].get().strip()
        try:
            quantity = int(values["qty"].get())
            if not raw_item or quantity <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Check the delivery", "Enter a Commodity Name and a whole quantity greater than zero.")
            return
            
        item = normalize_item_name(raw_item)
        if to_shelter:
            stock = shelters_accounts[self.user]["current_stock"]
            stock[item] = stock.get(item, 0) + quantity
            central_inventory.setdefault(item, 0)
            per_capita_ratios.setdefault(item, lambda total, injured: total)
            page = "Contribution"
        else:
            central_inventory[item] = central_inventory.get(item, 0) + quantity
            per_capita_ratios.setdefault(item, lambda total, injured: total)
            page = "Restock"
            
        save_state()
        messagebox.showinfo("Stock updated", f"Added {quantity:,} units of {item}.")
        self.show_page(page)

    def assessment_page(self):
        self.page_heading("Assess shelter needs", "Calculate per-capita requirements, gaps, and returnable surplus.")
        block = self.panel(self.content, "Shelter population")
        block.pack(fill="x", padx=32, pady=(0, 18))
        form = tk.Frame(block, bg="#FFFFFF")
        form.pack(fill="x", padx=18, pady=(0, 17))
        previous = last_requests.get(self.user, {})
        values = self.entry_form(form, [("People currently at shelter", "people", str(previous.get("people", ""))), ("People requiring medical care", "injured", str(previous.get("injured", "")))])
        result = tk.Frame(self.content, bg="#F3F5F1")
        result.pack(fill="both", expand=True, padx=32, pady=(0, 20))
        ttk.Button(form, text="Calculate needs", style="Primary.TButton", command=lambda: self.calculate_needs(values, result)).pack(anchor="w", pady=(17, 0))
        self.assessment_result = result

    def calculate_needs(self, values, result):
        try:
            people = int(values["people"].get())
            injured = int(values["injured"].get())
            if people <= 0 or injured < 0 or injured > people:
                raise ValueError
        except ValueError:
            messagebox.showerror("Check the assessment", "Enter a population above zero and an injured count between zero and the population.")
            return
            
        ratio = injured / people
        last_requests[self.user] = {"people": people, "injured": injured}
        save_state()
        
        urgency = "Critical" if ratio > 0.25 or people > 150 else "High" if ratio > 0.10 else "Moderate"
        shelter = shelters_accounts[self.user]
        rows = []
        for item, formula in per_capita_ratios.items():
            required = formula(people, injured)
            on_hand = shelter["current_stock"].get(item, 0)
            rows.append((item, required, on_hand, max(0, required - on_hand), max(0, on_hand - required)))
            
        self.assessment = {"urgency": urgency, "shelter": shelter["shelter_name"], "rows": rows}
        for child in result.winfo_children():
            child.destroy()
            
        tk.Label(result, text=f"{urgency.upper()} PRIORITY  /  {ratio:.0%} INJURY RATE", bg="#F3F5F1", fg="#A35B22" if urgency == "Critical" else "#596A5D", font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(0, 8))
        block = self.panel(result, "Requirement vs. on-hand")
        block.pack(fill="both", expand=True)
        
        table = self.make_table(block, [("item", "SUPPLY", 180), ("required", "REQUIRED", 100), ("onhand", "ON-HAND", 100), ("deficit", "DEFICIT", 100), ("surplus", "SURPLUS", 100)], 6)
        for item, required, on_hand, deficit, surplus in rows:
            table.insert("", "end", values=(item, required, on_hand, deficit, surplus))
            
        self.return_surplus = tk.BooleanVar(value=False)
        tk.Checkbutton(block, text="Return all calculated surplus to the central warehouse", variable=self.return_surplus, bg="#FFFFFF", fg="#425349", activebackground="#FFFFFF", selectcolor="#FFFFFF", font=("Segoe UI", 9)).pack(anchor="w", padx=15, pady=(0, 10))
        ttk.Button(block, text="Submit deficits", style="Primary.TButton", command=self.submit_assessment).pack(anchor="w", padx=15, pady=(0, 15))

    def submit_assessment(self):
        if not self.assessment:
            return
            
        deficits = 0
        for item, _required, _on_hand, deficit, surplus in self.assessment["rows"]:
            if deficit:
                requests_queue.append({"shelter": self.assessment["shelter"], "item": item, "qty": deficit, "urgency": self.assessment["urgency"], "status": "Pending"})
                deficits += 1
            if self.return_surplus.get() and surplus:
                stock = shelters_accounts[self.user]["current_stock"]
                central_inventory[item] = central_inventory.get(item, 0) + surplus
                stock[item] -= surplus
                
        save_state()
        messagebox.showinfo("Assessment submitted", f"Submitted {deficits} deficit request(s).")
        self.assessment = None
        self.show_page("Requests")

    def requests_page(self):
        title = "Needs & requests"
        subtitle = "Track requests submitted by shelters and their fulfillment status."
        self.page_heading(title, subtitle)
        block = self.panel(self.content, "Request register")
        block.pack(fill="both", expand=True, padx=32, pady=(0, 24))
        table = self.make_table(block, [("shelter", "SHELTER", 230), ("item", "SUPPLY", 150), ("qty", "QTY", 70), ("urgency", "PRIORITY", 110), ("status", "STATUS", 220)], 14)
        rows = requests_queue if self.role == "admin" else [request for request in requests_queue if request["shelter"] == shelters_accounts[self.user]["shelter_name"]]
        for request in reversed(rows):
            table.insert("", "end", values=(request["shelter"], request["item"], request["qty"], request["urgency"], request["status"]))

    def dispatch_page(self):
        self.page_heading("Dispatch queue", "Review open deficits and allocate central stock by urgency.")
        block = self.panel(self.content, "Pending requests")
        block.pack(fill="both", expand=True, padx=32, pady=(0, 16))
        table = self.make_table(block, [("shelter", "SHELTER", 230), ("item", "SUPPLY", 150), ("qty", "REQUESTED", 90), ("urgency", "PRIORITY", 110), ("stock", "CENTRAL AVAILABLE", 150)], 12)
        pending = sorted((request for request in requests_queue if request_is_open(request)), key=lambda request: urgency_rank[request["urgency"]])
        for request in pending:
            table.insert("", "end", values=(request["shelter"], request["item"], request["qty"], request["urgency"], central_inventory.get(request["item"], 0)))
        ttk.Button(self.content, text="Run priority dispatch", style="Primary.TButton", command=lambda: self.dispatch(table)).pack(anchor="w", padx=32, pady=(0, 22))

    def dispatch(self, table):
        pending = sorted((request for request in requests_queue if request_is_open(request)), key=lambda request: urgency_rank[request["urgency"]])
        if not pending:
            messagebox.showinfo("Queue is clear", "There are no pending requests to dispatch.")
            return
            
        fulfilled = 0
        partial = 0
        for request in pending:
            available = central_inventory.get(request["item"], 0)
            partially_fulfilled = request["status"].startswith("Partially fulfilled")
            if available >= request["qty"]:
                central_inventory[request["item"]] -= request["qty"]
                request["status"] = "Fulfilled"
                fulfilled += 1
            elif available > 0:
                request["status"] = f"Partially fulfilled ({available} sent)"
                request["qty"] -= available
                central_inventory[request["item"]] = 0
                partial += 1
            elif not partially_fulfilled:
                request["status"] = "Unfulfilled (out of stock)"
                
        save_state()
        messagebox.showinfo("Dispatch complete", f"Fulfilled: {fulfilled}   Partially fulfilled: {partial}\nOut-of-stock requests remain visible in the request register.")
        self.show_page("Dispatch")


def main():
    root = tk.Tk()
    app = ReliefApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
def assessment_row(item, people, injured, stock):
    if item == "Water":
        required = people * 2
    elif item == "First Aid Kits":
        required = injured
    else:
        required = people
    on_hand = stock.get(item, 0)
    return {
        "item": item,
        "required": required,
        "on_hand": on_hand,
        "deficit": max(0, required - on_hand),
        "surplus": max(0, on_hand - required),
    }


def assess_shelter(state, username, people, injured):
    stock = state["shelters"][username]["current_stock"]
    ratio = injured / people
    urgency = "Critical" if ratio > 0.25 or people > 150 else "High" if ratio > 0.10 else "Moderate"
    rows = [
        assessment_row(item, people, injured, stock)
        for item in state["central_inventory"]
    ]
    return {"urgency": urgency, "injury_rate": ratio, "rows": rows}


def record_assessment_row(state, username, shelter_name, urgency, row, return_surplus):
    deficit = row["deficit"]
    if deficit:
        state["requests"].append({
            "shelter": shelter_name,
            "item": row["item"],
            "qty": deficit,
            "urgency": urgency,
            "status": "Pending",
        })
    if return_surplus and row["surplus"]:
        item = row["item"]
        state["central_inventory"][item] = state["central_inventory"].get(item, 0) + row["surplus"]
        state["shelters"][username]["current_stock"][item] -= row["surplus"]
    return bool(deficit)


def request_is_open(item):
    status = item.get("status", "")
    return status == "Pending" or status.startswith("Partially fulfilled")


def dispatch_request(state, item):
    available = state["central_inventory"].get(item["item"], 0)
    requested = item["qty"]
    partially_fulfilled = item["status"].startswith("Partially fulfilled")
    if available >= requested:
        state["central_inventory"][item["item"]] -= requested
        item["status"] = "Fulfilled"
        return "fulfilled"
    if available > 0:
        item["status"] = f"Partially fulfilled ({available} sent)"
        item["qty"] = requested - available
        state["central_inventory"][item["item"]] = 0
        return "partial"
    if not partially_fulfilled:
        item["status"] = "Unfulfilled (out of stock)"
    return "unfulfilled"


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        registered_account = g.db.execute(
            "SELECT password_hash, display_name FROM user_accounts WHERE username = ?",
            (username,),
        ).fetchone()
        account = ACCOUNTS.get(username)
        if registered_account and check_password_hash(registered_account["password_hash"], password):
            role = "shelter"
            display_name = registered_account["display_name"]
        elif account and secrets.compare_digest(password, account["password"]):
            role = account["role"]
            display_name = account["name"]
        else:
            flash("Sign-in failed. Check your username and password.", "error")
            return render_template("login.html", show_demo_credentials=not IS_PRODUCTION, is_registration=False)

        session.clear()
        session["username"] = username
        session["role"] = role
        session["display_name"] = display_name
        return redirect(url_for("dashboard"))
    return render_template("login.html", show_demo_credentials=not IS_PRODUCTION, is_registration=False)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        if not display_name or len(display_name) > 80:
            flash("Enter a shelter or organization name of 1 to 80 characters.", "error")
        elif not re.fullmatch(r"[a-z0-9_]{3,30}", username):
            flash("Username must be 3 to 30 characters using letters, numbers, and underscores.", "error")
        elif len(password) < 8 or len(password) > 128:
            flash("Password must be between 8 and 128 characters.", "error")
        elif username in ACCOUNTS or g.db.execute(
            "SELECT 1 FROM user_accounts WHERE username = ?",
            (username,),
        ).fetchone():
            flash("That username is already taken.", "error")
        else:
            g.db.execute(
                "INSERT INTO user_accounts (username, password_hash, display_name) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), display_name),
            )
            g.state["shelters"][username] = {
                "name": display_name,
                "current_stock": dict.fromkeys(g.state["central_inventory"], 0),
            }
            flash("Your shelter account is ready. Sign in to continue.", "success")
            return redirect(url_for("login"))

    return render_template("login.html", show_demo_credentials=False, is_registration=True)


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    state = current_state()
    username = session["username"]
    shelter = state["shelters"].get(username) if session["role"] == "shelter" else None
    visible_requests = state["requests"]
    if shelter:
        visible_requests = [item for item in visible_requests if item["shelter"] == shelter["name"]]
        metrics = [
            ("Supply On-Hand", sum(shelter["current_stock"].values()), "Across your shelter supplies"),
            ("Supply categories", len(shelter["current_stock"]), "Tracked at your location"),
            ("Current Requests", sum(request_is_open(item) for item in visible_requests), "Waiting for dispatch"),
        ]
        title = shelter["name"]
    else:
        pending = [item for item in state["requests"] if request_is_open(item)]
        metrics = [
            ("Central stock", sum(state["central_inventory"].values()), "Units ready for response"),
            ("Pending requests", len(pending), "Across all shelters"),
            ("Critical", sum(item["urgency"] == "Critical" for item in pending), "Need immediate attention"),
        ]
        title = "Operation Hub"
    latest = list(reversed(visible_requests[-12:]))
    return render_template("dashboard.html", title=title, metrics=metrics, requests=latest)


@app.route("/inventory")
@login_required
def inventory():
    state = current_state()
    shelter = state["shelters"].get(current_shelter_key()) if session["role"] == "shelter" else None
    rows = []
    for item, central in state["central_inventory"].items():
        on_hand = shelter["current_stock"].get(item, 0) if shelter else None
        rows.append({"item": item, "central": central, "on_hand": on_hand})
    return render_template("inventory.html", rows=rows, is_shelter=bool(shelter))


@app.route("/requests")
@login_required
def requests_page():
    state = current_state()
    rows = state["requests"]
    if session["role"] == "shelter":
        shelter_name = state["shelters"][current_shelter_key()]["name"]
        rows = [item for item in rows if item["shelter"] == shelter_name]
    return render_template("requests.html", requests=list(reversed(rows)))


@app.post("/requests/clear")
@role_required("admin")
def clear_request_activity():
    state = current_state()
    request_count = len(state["requests"])
    assessment_count = len(state["last_requests"])
    state["requests"].clear()
    state["last_requests"].clear()
    flash(f"Cleared {request_count} requests and {assessment_count} saved requests.", "success")
    return redirect(url_for("requests_page"))


@app.route("/restock", methods=["GET", "POST"])
@role_required("admin")
def restock():
    state = current_state()
    if request.method == "POST":
        item_name = request.form.get("item", "").strip()
        quantity = parse_positive_integer(request.form.get("quantity"))
        if not item_name or quantity is None:
            flash("Enter a Commodity Name and a whole quantity greater than zero.", "error")
        else:
            item = normalize_web_item_name(item_name, state["central_inventory"])
            state["central_inventory"][item] = state["central_inventory"].get(item, 0) + quantity
            flash(f"Added {quantity:,} units of {item} to central stock.", "success")
            return redirect(url_for("inventory"))
    return render_template(
        "stock_form.html",
        title="Restock warehouse",
        description="Record Deliveries and Maintain Current Inventory",
        form_heading="Add stock to the warehouse",
        submit_label="Add to central inventory",
    )


@app.route("/contributions", methods=["GET", "POST"])
@role_required("shelter")
def contributions():
    state = current_state()
    if request.method == "POST":
        item_name = request.form.get("item", "").strip()
        quantity = parse_positive_integer(request.form.get("quantity"))
        if not item_name or quantity is None:
            flash("Enter a Commodity Name and a whole quantity greater than zero.", "error")
        else:
            item = normalize_web_item_name(item_name, state["central_inventory"])
            stock = state["shelters"][current_shelter_key()]["current_stock"]
            stock[item] = stock.get(item, 0) + quantity
            state["central_inventory"].setdefault(item, 0)
            flash(f"Added {quantity:,} units of {item} to shelter stock.", "success")
            return redirect(url_for("inventory"))
    return render_template(
        "stock_form.html",
        title="Supply contribution",
        description="Add donated or externally sourced supplies to your shelter.",
        form_heading="Record Supply contribution",
        submit_label="Add contribution",
    )


@app.route("/assessment", methods=["GET", "POST"])
@role_required("shelter")
def assessment():
    state = current_state()
    username = current_shelter_key()
    previous = state["last_requests"].get(username, {})
    people_value = previous.get("people", "")
    injured_value = previous.get("injured", "")
    results = None
    if request.method == "POST":
        people = parse_positive_integer(request.form.get("people"))
        injured_text = request.form.get("injured", "")
        try:
            injured = int(injured_text)
        except (TypeError, ValueError):
            injured = -1
        if people is None or injured < 0 or injured > people:
            flash("Enter a population above zero and an injured count between zero and the population.", "error")
        else:
            people_value, injured_value = people, injured
            state["last_requests"][username] = {"people": people, "injured": injured}
            results = assess_shelter(state, username, people, injured)
    return render_template(
        "assessment.html",
        people=people_value,
        injured=injured_value,
        results=results,
    )


@app.post("/assessment/submit")
@role_required("shelter")
def submit_assessment():
    state = current_state()
    username = current_shelter_key()
    previous = state["last_requests"].get(username)
    if not previous:
        flash("Calculate the shelter assessment before submitting it.", "error")
        return redirect(url_for("assessment"))
    result = assess_shelter(state, username, previous["people"], previous["injured"])
    shelter_name = state["shelters"][username]["name"]
    return_surplus = request.form.get("return_surplus") == "on"
    submitted = sum(
        record_assessment_row(state, username, shelter_name, result["urgency"], row, return_surplus)
        for row in result["rows"]
    )
    flash(f"Assessment submitted with {submitted} deficit request(s).", "success")
    return redirect(url_for("requests_page"))


@app.route("/dispatch", methods=["GET", "POST"])
@role_required("admin")
def dispatch():
    state = current_state()
    pending = [item for item in state["requests"] if request_is_open(item)]
    pending.sort(key=lambda item: {"Critical": 1, "High": 2, "Moderate": 3}.get(item["urgency"], 4))
    if request.method == "POST":
        if not pending:
            flash("There are no pending requests to dispatch.", "error")
            return redirect(url_for("dispatch"))
        outcomes = {"fulfilled": 0, "partial": 0}
        for item in pending:
            outcome = dispatch_request(state, item)
            if outcome in outcomes:
                outcomes[outcome] += 1
        fulfilled = outcomes["fulfilled"]
        partial = outcomes["partial"]
        flash(f"Dispatch complete: {fulfilled} fulfilled, {partial} partially fulfilled.", "success")
        return redirect(url_for("dispatch"))
    return render_template("dispatch.html", requests=pending, inventory=state["central_inventory"])


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="You do not have access to this operation."), 403


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=error.description), 400


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5001")))