#!/usr/bin/env python3
"""Stock Management System - single-file CLI with JSON storage and email orders.

Python 3.10+ recommended. Uses only the Python standard library.
Run: python stock_manager.py
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
BACKUP_DIR = APP_DIR / "backups"

DATA_FILES = {
    "stock": APP_DIR / "stock.json",
    "suppliers": APP_DIR / "suppliers.json",
    "orders": APP_DIR / "orders.json",
    "movements": APP_DIR / "movements.json",
    "settings": APP_DIR / "settings.json",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "currency_symbol": "£",
    "email": {
        "smtp_host": "",
        "smtp_port": 587,
        "use_starttls": True,
        "sender": "",
        "username": "",
        "password_env": "STOCK_EMAIL_PASSWORD",
    },
}

LIST_KEYS = ("stock", "suppliers", "orders", "movements")


class DataFileError(Exception):
    """Raised when an existing data file cannot safely be loaded."""


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def save_json(path: Path, value: Any) -> None:
    """Save JSON via a temporary file and atomic replacement."""
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=4, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        save_json(path, default)
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataFileError(
            f"Cannot read {path.name}: {exc}. The file has not been overwritten. "
            "Correct or restore the JSON file, then restart the program."
        ) from exc


def load_data() -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, path in DATA_FILES.items():
        default = {} if key == "settings" else []
        data[key] = load_json(path, default)
        expected = dict if key == "settings" else list
        if not isinstance(data[key], expected):
            raise DataFileError(
                f"{path.name} must contain a JSON {expected.__name__}. "
                "The file has not been changed."
            )
    settings = data["settings"]
    settings.setdefault("currency_symbol", DEFAULT_SETTINGS["currency_symbol"])
    settings.setdefault("email", {})
    if not isinstance(settings["email"], dict):
        raise DataFileError("The 'email' setting in settings.json must be an object.")
    for key, value in DEFAULT_SETTINGS["email"].items():
        settings["email"].setdefault(key, value)
    return data


def save_data(data: dict[str, Any], *keys: str) -> None:
    for key in keys or tuple(DATA_FILES):
        save_json(DATA_FILES[key], data[key])


def create_backup() -> Path:
    """Create a timestamped snapshot of all application JSON data files."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_name = datetime.now().astimezone().strftime("backup_%Y%m%d_%H%M%S_%f")
    destination = BACKUP_DIR / backup_name
    destination.mkdir()
    try:
        for path in DATA_FILES.values():
            if path.exists():
                shutil.copy2(path, destination / path.name)
            else:
                raise FileNotFoundError(f"Required data file is missing: {path.name}")
    except OSError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def create_daily_backup() -> Path | None:
    """Create one automatic snapshot per calendar day, if one does not exist yet."""
    today = datetime.now().astimezone().strftime("%Y%m%d")
    if any(path.name.startswith(f"backup_{today}_") for path in available_backups()):
        return None
    return create_backup()


def available_backups() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted((path for path in BACKUP_DIR.iterdir()
                   if path.is_dir() and path.name.startswith("backup_")), reverse=True)


def validate_backup(backup_path: Path) -> None:
    """Check that every required backup file is readable and has the right top-level type."""
    for key, original_path in DATA_FILES.items():
        backup_file = backup_path / original_path.name
        if not backup_file.is_file():
            raise DataFileError(f"Backup is incomplete: {original_path.name} is missing.")
        try:
            with backup_file.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise DataFileError(f"Backup file {original_path.name} cannot be read: {exc}") from exc
        expected = dict if key == "settings" else list
        if not isinstance(value, expected):
            raise DataFileError(f"Backup file {original_path.name} must contain a JSON {expected.__name__}.")
        if key == "settings" and "email" in value and not isinstance(value["email"], dict):
            raise DataFileError("Backup settings.json contains an invalid email setting.")


def restore_backup(backup_path: Path) -> None:
    """Restore a validated snapshot, first creating a safety backup of current data."""
    validate_backup(backup_path)
    safety_path = create_backup()
    try:
        for original_path in DATA_FILES.values():
            source = backup_path / original_path.name
            with source.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            save_json(original_path, value)
    except (OSError, json.JSONDecodeError):
        raise DataFileError(
            f"Restore did not complete. Your pre-restore safety backup is {safety_path.name}."
        )


def data_backup_menu(data: dict[str, Any]) -> dict[str, Any]:
    while True:
        print_header("DATA BACKUP AND RESTORE")
        print("1. Create a backup now")
        print("2. List available backups")
        print("3. Restore a backup")
        print("0. Return to main menu")
        choice = input("Select: ").strip()
        if choice == "1":
            backup = create_backup()
            print(f"Backup created: {backup.relative_to(APP_DIR)}")
        elif choice == "2":
            backups = available_backups()
            if not backups:
                print("No backups found.")
            else:
                for index, backup in enumerate(backups, start=1):
                    print(f"{index}. {backup.name}")
        elif choice == "3":
            backups = available_backups()
            if not backups:
                print("No backups are available to restore.")
                continue
            print("Available backups (newest first):")
            for index, backup in enumerate(backups, start=1):
                print(f"{index}. {backup.name}")
            selection = ask_int("Backup number to restore (0 cancels): ", 0)
            if selection == 0:
                print("Restore cancelled.")
                continue
            if selection > len(backups):
                print("That backup number does not exist.")
                continue
            chosen = backups[selection - 1]
            if yes_no(f"Restore {chosen.name}? Current data will be backed up first."):
                restore_backup(chosen)
                data = load_data()
                print("Backup restored successfully. Application data reloaded.")
        elif choice == "0":
            return data
        else:
            print("Invalid selection.")


def pause() -> None:
    input("\nPress Enter to continue...")


def ask_text(prompt: str, *, current: str | None = None,
             allow_blank: bool = False) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        if current is not None:
            return current
        if allow_blank:
            return ""
        print("This field is required.")


def ask_int(prompt: str, minimum: int = 0, current: int | None = None) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw and current is not None:
            return current
        try:
            value = int(raw)
            if value < minimum:
                print(f"Enter a whole number of at least {minimum}.")
                continue
            return value
        except ValueError:
            print("Enter a valid whole number.")


def ask_money(prompt: str, current: float | None = None) -> float:
    while True:
        raw = input(prompt).strip().replace(",", ".")
        if not raw and current is not None:
            return current
        try:
            value = float(raw)
            if not math.isfinite(value) or value < 0:
                raise ValueError
            return round(value, 2)
        except ValueError:
            print("Enter a valid non-negative amount, for example 12.50.")


def yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    answer = input(f"{prompt} ({suffix}): ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def find_record(records: list[dict[str, Any]], record_id: str) -> dict[str, Any] | None:
    wanted = record_id.strip().casefold()
    return next((record for record in records
                 if str(record.get("id", "")).casefold() == wanted), None)


def next_id(prefix: str, records: list[dict[str, Any]]) -> str:
    highest = 0
    pattern = re.compile(rf"{re.escape(prefix)}-(\d+)$", re.IGNORECASE)
    for record in records:
        match = pattern.fullmatch(str(record.get("id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1:04d}"


def currency(data: dict[str, Any], amount: float) -> str:
    return f"{data['settings'].get('currency_symbol', '£')}{amount:,.2f}"


def low_stock_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    # Minimum stock is a trigger: an item is low when quantity is at or below it.
    return [item for item in data["stock"]
            if int(item.get("quantity", 0)) <= int(item.get("minimum", 0))]


def has_open_order_for_item(data: dict[str, Any], item_id: str) -> bool:
    return any(order.get("status") in ("DRAFT", "SENT") and
               any(line.get("item_id") == item_id for line in order.get("items", []))
               for order in data["orders"])


def add_movement(data: dict[str, Any], item: dict[str, Any], kind: str,
                 quantity: int, reference: str = "") -> None:
    data["movements"].append({
        "id": next_id("MOV", data["movements"]),
        "timestamp": timestamp(),
        "item_id": item["id"],
        "item_name": item["name"],
        "type": kind,
        "quantity": quantity,
        "balance_after": item["quantity"],
        "reference": reference,
    })


def print_header(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"{title:^72}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Stock item management
# ---------------------------------------------------------------------------

def show_stock(data: dict[str, Any], records: list[dict[str, Any]] | None = None) -> None:
    records = data["stock"] if records is None else records
    if not records:
        print("No stock items found.")
        return
    print(f"\n{'ID':<13}{'PRODUCT':<25}{'QTY':>7}{'MIN':>7}{'REORDER':>9}  {'STATUS':<9}")
    print("-" * 72)
    for item in sorted(records, key=lambda row: row["id"].casefold()):
        status = "LOW" if item["quantity"] <= item["minimum"] else "OK"
        print(f"{item['id']:<13}{item['name'][:24]:<25}{item['quantity']:>7}"
              f"{item['minimum']:>7}{item['reorder_quantity']:>9}  {status:<9}")


def add_stock_item(data: dict[str, Any]) -> None:
    print_header("ADD STOCK ITEM")
    item_id = ask_text("Product ID / SKU: ")
    if find_record(data["stock"], item_id):
        print("That product ID is already in use.")
        return
    name = ask_text("Product name: ")
    description = ask_text("Description (optional): ", allow_blank=True)
    category = ask_text("Category (optional): ", allow_blank=True)
    quantity = ask_int("Opening quantity: ", 0)
    minimum = ask_int("Minimum stock level: ", 0)
    reorder = ask_int("Reorder quantity (amount to order): ", 1)
    unit_cost = ask_money("Unit cost: ")
    supplier_id = ask_text("Supplier ID (optional): ", allow_blank=True)
    if supplier_id and not find_record(data["suppliers"], supplier_id):
        print("Supplier not found; no supplier has been assigned.")
        supplier_id = ""
    item = {
        "id": item_id, "name": name, "description": description,
        "category": category, "quantity": quantity, "minimum": minimum,
        "reorder_quantity": reorder, "unit_cost": unit_cost,
        "supplier_id": supplier_id, "created_at": timestamp(),
        "updated_at": timestamp(),
    }
    data["stock"].append(item)
    if quantity:
        add_movement(data, item, "OPENING", quantity, "Opening balance")
    save_data(data, "stock", "movements")
    print("Stock item added.")


def edit_stock_item(data: dict[str, Any]) -> None:
    item = find_record(data["stock"], ask_text("Product ID / SKU: "))
    if not item:
        print("Product not found.")
        return
    print("Leave a text or numeric field blank to keep its current value.")
    item["name"] = ask_text(f"Name [{item['name']}]: ", current=item["name"])
    item["description"] = ask_text(
        f"Description [{item['description']}]: ", current=item["description"], allow_blank=True)
    item["category"] = ask_text(
        f"Category [{item['category']}]: ", current=item["category"], allow_blank=True)
    item["minimum"] = ask_int(f"Minimum level [{item['minimum']}]: ", 0, item["minimum"])
    item["reorder_quantity"] = ask_int(
        f"Reorder quantity [{item['reorder_quantity']}]: ", 1, item["reorder_quantity"])
    item["unit_cost"] = ask_money(f"Unit cost [{item['unit_cost']:.2f}]: ", item["unit_cost"])
    supplier_text = input(f"Supplier ID [{item.get('supplier_id', '')}] (type NONE to clear): ").strip()
    if supplier_text:
        if supplier_text.casefold() == "none":
            item["supplier_id"] = ""
        elif find_record(data["suppliers"], supplier_text):
            item["supplier_id"] = supplier_text
        else:
            print("Supplier not found; existing supplier retained.")
    item["updated_at"] = timestamp()
    save_data(data, "stock")
    print("Stock item updated.")


def delete_stock_item(data: dict[str, Any]) -> None:
    item = find_record(data["stock"], ask_text("Product ID / SKU: "))
    if not item:
        print("Product not found.")
        return
    if has_open_order_for_item(data, item["id"]):
        print("Cannot delete this item while it appears on a draft or sent purchase order.")
        return
    if yes_no(f"Permanently delete {item['name']} ({item['id']})?"):
        data["stock"].remove(item)
        save_data(data, "stock")
        print("Stock item deleted. Historical movement records are retained.")


def stock_item_details(data: dict[str, Any]) -> None:
    item = find_record(data["stock"], ask_text("Product ID / SKU: "))
    if not item:
        print("Product not found.")
        return
    supplier = find_record(data["suppliers"], item.get("supplier_id", ""))
    for key, value in item.items():
        label = key.replace("_", " ").title()
        if key == "unit_cost":
            value = currency(data, value)
        if key == "supplier_id":
            value = f"{value} ({supplier['company']})" if supplier else (value or "Not assigned")
        print(f"{label}: {value}")
    print(f"Stock status: {'LOW STOCK' if item['quantity'] <= item['minimum'] else 'OK'}")


def stock_menu(data: dict[str, Any]) -> None:
    while True:
        print_header("STOCK MANAGEMENT")
        print("1. Add stock item\n2. Edit stock item\n3. Delete stock item")
        print("4. View item details\n0. Return to main menu")
        choice = input("Select: ").strip()
        if choice == "1":
            add_stock_item(data)
        elif choice == "2":
            edit_stock_item(data)
        elif choice == "3":
            delete_stock_item(data)
        elif choice == "4":
            stock_item_details(data)
        elif choice == "0":
            return
        else:
            print("Invalid selection.")


# ---------------------------------------------------------------------------
# Supplier management
# ---------------------------------------------------------------------------

def valid_email(address: str) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address))


def supplier_menu(data: dict[str, Any]) -> None:
    while True:
        print_header("SUPPLIER MANAGEMENT")
        print("1. Add supplier\n2. Edit supplier\n3. Delete supplier")
        print("4. View all suppliers\n0. Return to main menu")
        choice = input("Select: ").strip()
        if choice == "1":
            supplier_id = ask_text("Supplier ID: ")
            if find_record(data["suppliers"], supplier_id):
                print("That supplier ID is already in use.")
                continue
            company = ask_text("Company name: ")
            contact = ask_text("Contact person (optional): ", allow_blank=True)
            email = ask_text("Email address (optional): ", allow_blank=True)
            if email and not valid_email(email):
                print("That email address does not look valid; supplier not added.")
                continue
            supplier = {
                "id": supplier_id, "company": company, "contact": contact,
                "email": email, "phone": ask_text("Telephone (optional): ", allow_blank=True),
                "address": ask_text("Address (optional): ", allow_blank=True),
            }
            data["suppliers"].append(supplier)
            save_data(data, "suppliers")
            print("Supplier added.")
        elif choice == "2":
            supplier = find_record(data["suppliers"], ask_text("Supplier ID: "))
            if not supplier:
                print("Supplier not found.")
                continue
            for field, label in (("company", "Company"), ("contact", "Contact person"),
                                 ("phone", "Telephone"), ("address", "Address")):
                supplier[field] = ask_text(
                    f"{label} [{supplier.get(field, '')}]: ",
                    current=supplier.get(field, ""), allow_blank=True)
            email = ask_text(f"Email [{supplier.get('email', '')}]: ",
                             current=supplier.get("email", ""), allow_blank=True)
            if email and not valid_email(email):
                print("Invalid email; existing address retained.")
            else:
                supplier["email"] = email
            save_data(data, "suppliers")
            print("Supplier updated.")
        elif choice == "3":
            supplier = find_record(data["suppliers"], ask_text("Supplier ID: "))
            if not supplier:
                print("Supplier not found.")
                continue
            assigned = any(item.get("supplier_id", "").casefold() == supplier["id"].casefold()
                           for item in data["stock"])
            open_order = any(order.get("supplier_id", "").casefold() == supplier["id"].casefold()
                             and order.get("status") in ("DRAFT", "SENT")
                             for order in data["orders"])
            if assigned or open_order:
                print("Cannot delete: supplier is assigned to stock or has an open purchase order.")
            elif yes_no(f"Delete supplier {supplier['company']}?"):
                data["suppliers"].remove(supplier)
                save_data(data, "suppliers")
                print("Supplier deleted.")
        elif choice == "4":
            if not data["suppliers"]:
                print("No suppliers recorded.")
            for supplier in data["suppliers"]:
                print(f"{supplier['id']:<12} {supplier['company']:<28} "
                      f"{supplier.get('email', '')}")
        elif choice == "0":
            return
        else:
            print("Invalid selection.")


# ---------------------------------------------------------------------------
# Stock in / stock out and movement history
# ---------------------------------------------------------------------------

def stock_transaction(data: dict[str, Any], direction: str) -> None:
    print_header("STOCK IN" if direction == "IN" else "STOCK OUT")
    item = find_record(data["stock"], ask_text("Product ID / SKU: "))
    if not item:
        print("Product not found.")
        return
    quantity = ask_int("Quantity: ", 1)
    if direction == "OUT" and quantity > item["quantity"]:
        print(f"Not enough stock. Current available quantity: {item['quantity']}.")
        return
    reference = ask_text("Reference / reason (optional): ", allow_blank=True)
    item["quantity"] += quantity if direction == "IN" else -quantity
    item["updated_at"] = timestamp()
    add_movement(data, item, direction, quantity, reference)
    save_data(data, "stock", "movements")
    print(f"Transaction recorded. New quantity: {item['quantity']}.")
    if item["quantity"] <= item["minimum"]:
        print("*** LOW STOCK ALERT: this item is at or below its minimum level. ***")


def movement_history(data: dict[str, Any]) -> None:
    if not data["movements"]:
        print("No stock movements recorded.")
        return
    print_header("RECENT STOCK MOVEMENTS")
    for move in reversed(data["movements"][-100:]):
        print(f"{move['timestamp']} | {move['item_id']:<12} {move['type']:<7} "
              f"{move['quantity']:>6} | balance {move['balance_after']:>6} | "
              f"{move.get('reference', '')}")
    print("Showing the latest 100 movements at most.")


# ---------------------------------------------------------------------------
# Search and reports
# ---------------------------------------------------------------------------

def search_stock(data: dict[str, Any]) -> None:
    term = ask_text("Search by ID, name, description or category: ").casefold()
    matches = [item for item in data["stock"]
               if term in " ".join(str(item.get(field, "")) for field in
                                    ("id", "name", "description", "category")).casefold()]
    show_stock(data, matches)


def low_stock_report(data: dict[str, Any]) -> None:
    print_header("LOW STOCK REPORT")
    low = low_stock_items(data)
    show_stock(data, low)
    if low:
        print("\nReorder quantity is the configured number of units to request per item.")
        for item in low:
            supplier = find_record(data["suppliers"], item.get("supplier_id", ""))
            print(f"{item['id']}: request {item['reorder_quantity']} unit(s); "
                  f"supplier: {supplier['company'] if supplier else 'NOT ASSIGNED'}")


def reports(data: dict[str, Any]) -> None:
    total_units = sum(item["quantity"] for item in data["stock"])
    total_value = sum(item["quantity"] * item["unit_cost"] for item in data["stock"])
    low_count = len(low_stock_items(data))
    open_orders = sum(order["status"] in ("DRAFT", "SENT") for order in data["orders"])
    print_header("INVENTORY SUMMARY")
    print(f"Product lines:       {len(data['stock'])}")
    print(f"Total units:         {total_units}")
    print(f"Low-stock products:  {low_count}")
    print(f"Inventory value:    {currency(data, total_value)}")
    print(f"Suppliers:           {len(data['suppliers'])}")
    print(f"Purchase orders:     {len(data['orders'])}")
    print(f"Open purchase orders:{open_orders:>5}")


# ---------------------------------------------------------------------------
# CSV report exports
# ---------------------------------------------------------------------------

def export_csv_report(data: dict[str, Any], report_type: str) -> Path:
    """Export one report to a timestamped CSV file in the exports folder."""
    exports_dir = APP_DIR / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    destination = exports_dir / f"{report_type}_{stamp}.csv"

    suppliers = {supplier["id"]: supplier.get("company", "")
                 for supplier in data["suppliers"]}

    if report_type in ("inventory", "low_stock"):
        fields = ["item_id", "name", "description", "category", "quantity",
                  "minimum", "reorder_quantity", "unit_cost", "stock_value",
                  "supplier_id", "supplier"]
        records = data["stock"]
        if report_type == "low_stock":
            records = low_stock_items(data)
        rows = [{
            "item_id": item.get("id", ""),
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "category": item.get("category", ""),
            "quantity": item.get("quantity", 0),
            "minimum": item.get("minimum", 0),
            "reorder_quantity": item.get("reorder_quantity", 0),
            "unit_cost": item.get("unit_cost", 0),
            "stock_value": round(item.get("quantity", 0) * item.get("unit_cost", 0), 2),
            "supplier_id": item.get("supplier_id", ""),
            "supplier": suppliers.get(item.get("supplier_id", ""), ""),
        } for item in records]
    elif report_type == "suppliers":
        fields = ["supplier_id", "company", "contact", "email", "phone", "address"]
        rows = [{
            "supplier_id": supplier.get("id", ""),
            "company": supplier.get("company", ""),
            "contact": supplier.get("contact", ""),
            "email": supplier.get("email", ""),
            "phone": supplier.get("phone", ""),
            "address": supplier.get("address", ""),
        } for supplier in data["suppliers"]]
    elif report_type == "movements":
        fields = ["movement_id", "timestamp", "item_id", "item_name", "type",
                  "quantity", "balance_after", "reference"]
        rows = [{
            "movement_id": move.get("id", ""),
            "timestamp": move.get("timestamp", ""),
            "item_id": move.get("item_id", ""),
            "item_name": move.get("item_name", ""),
            "type": move.get("type", ""),
            "quantity": move.get("quantity", 0),
            "balance_after": move.get("balance_after", 0),
            "reference": move.get("reference", ""),
        } for move in data["movements"]]
    elif report_type == "orders":
        fields = ["order_id", "created_at", "sent_at", "received_at", "status",
                  "supplier_id", "supplier", "item_id", "item_name", "quantity",
                  "unit_cost", "line_total", "order_total", "sent_method"]
        rows = []
        for order in data["orders"]:
            for line in order.get("items", []):
                rows.append({
                    "order_id": order.get("id", ""),
                    "created_at": order.get("created_at", ""),
                    "sent_at": order.get("sent_at", ""),
                    "received_at": order.get("received_at", ""),
                    "status": order.get("status", ""),
                    "supplier_id": order.get("supplier_id", ""),
                    "supplier": suppliers.get(order.get("supplier_id", ""), ""),
                    "item_id": line.get("item_id", ""),
                    "item_name": line.get("name", ""),
                    "quantity": line.get("quantity", 0),
                    "unit_cost": line.get("unit_cost", 0),
                    "line_total": round(line.get("quantity", 0) * line.get("unit_cost", 0), 2),
                    "order_total": order.get("total", 0),
                    "sent_method": order.get("sent_method", ""),
                })
    else:
        raise ValueError(f"Unknown CSV report type: {report_type}")

    # utf-8-sig makes the files open cleanly in common spreadsheet programs.
    with destination.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return destination


def csv_export_menu(data: dict[str, Any]) -> None:
    report_types = {
        "1": ("inventory", "Complete inventory"),
        "2": ("suppliers", "Supplier list"),
        "3": ("low_stock", "Low-stock items"),
        "4": ("movements", "Stock movement history"),
        "5": ("orders", "Purchase order history"),
    }
    while True:
        print_header("CSV REPORT EXPORT")
        print("1. Export complete inventory")
        print("2. Export supplier list")
        print("3. Export low-stock items")
        print("4. Export stock movement history")
        print("5. Export purchase order history")
        print("6. Export all reports")
        print("0. Return to main menu")
        choice = input("Select: ").strip()
        if choice == "0":
            return
        selected = list(report_types.values()) if choice == "6" else [report_types.get(choice)]
        if selected == [None]:
            print("Invalid selection.")
            continue
        try:
            for report_type, label in selected:
                path = export_csv_report(data, report_type)
                print(f"{label} exported: {path.relative_to(APP_DIR)}")
        except OSError as exc:
            print(f"CSV export failed: {exc}")
        except (KeyError, TypeError, ValueError) as exc:
            print(f"Could not create CSV report: {exc}")


# ---------------------------------------------------------------------------
# Purchase orders
# ---------------------------------------------------------------------------

def create_low_stock_orders(data: dict[str, Any]) -> list[dict[str, Any]]:
    low = low_stock_items(data)
    if not low:
        print("There are no low-stock items to order.")
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in low:
        supplier_id = item.get("supplier_id", "")
        supplier = find_record(data["suppliers"], supplier_id) if supplier_id else None
        if not supplier:
            print(f"Skipped {item['id']} - {item['name']}: no valid supplier assigned.")
            continue
        if has_open_order_for_item(data, item["id"]):
            print(f"Skipped {item['id']} - {item['name']}: already on a draft/sent order.")
            continue
        grouped.setdefault(supplier["id"], []).append(item)

    created: list[dict[str, Any]] = []
    for supplier_id, items in grouped.items():
        lines = [{
            "item_id": item["id"],
            "name": item["name"],
            "quantity": item["reorder_quantity"],
            "unit_cost": item["unit_cost"],
        } for item in items]
        order = {
            "id": next_id("PO", data["orders"]),
            "created_at": timestamp(),
            "supplier_id": supplier_id,
            "status": "DRAFT",
            "items": lines,
            "total": round(sum(line["quantity"] * line["unit_cost"] for line in lines), 2),
        }
        data["orders"].append(order)
        created.append(order)
    if created:
        save_data(data, "orders")
        for order in created:
            supplier = find_record(data["suppliers"], order["supplier_id"])
            print(f"Created {order['id']} for {supplier['company']} - "
                  f"{currency(data, order['total'])}.")
    elif not grouped:
        print("No purchase orders were created.")
    return created


def show_order(data: dict[str, Any], order: dict[str, Any]) -> None:
    supplier = find_record(data["suppliers"], order["supplier_id"])
    print(f"\n{order['id']} | {supplier['company'] if supplier else 'Unknown supplier'} | "
          f"{order['status']} | {currency(data, order['total'])}")
    print(f"Created: {order.get('created_at', 'Unknown')}")
    for line in order["items"]:
        line_total = line["quantity"] * line["unit_cost"]
        print(f"  {line['item_id']:<12} {line['name']:<28} x {line['quantity']:<5} "
              f"{currency(data, line_total)}")


def list_orders(data: dict[str, Any], status: str | None = None) -> list[dict[str, Any]]:
    orders = [order for order in data["orders"]
              if status is None or order["status"] == status]
    if not orders:
        print("No matching purchase orders.")
    else:
        for order in orders:
            show_order(data, order)
    return orders


def get_order(data: dict[str, Any]) -> dict[str, Any] | None:
    order = find_record(data["orders"], ask_text("Purchase order ID: "))
    if not order:
        print("Purchase order not found.")
    return order


def mark_order_sent(data: dict[str, Any], order: dict[str, Any], method: str) -> None:
    order["status"] = "SENT"
    order["sent_at"] = timestamp()
    order["sent_method"] = method
    save_data(data, "orders")
    print(f"{order['id']} marked as SENT.")


def receive_order(data: dict[str, Any], order: dict[str, Any]) -> None:
    if order["status"] != "SENT":
        print("Only a SENT order can be received. Mark it as sent first.")
        return
    print("This will add all ordered quantities to stock:")
    for line in order["items"]:
        print(f"  {line['item_id']} - {line['name']}: +{line['quantity']}")
    if not yes_no("Confirm that the complete order has arrived?"):
        print("Receipt cancelled; no stock was changed.")
        return
    for line in order["items"]:
        item = find_record(data["stock"], line["item_id"])
        if item:
            item["quantity"] += line["quantity"]
            item["updated_at"] = timestamp()
            add_movement(data, item, "IN", line["quantity"], f"Received {order['id']}")
        else:
            print(f"Warning: stock item {line['item_id']} no longer exists; skipped.")
    order["status"] = "RECEIVED"
    order["received_at"] = timestamp()
    save_data(data, "stock", "movements", "orders")
    print("Purchase order received and stock updated.")


def purchase_order_menu(data: dict[str, Any]) -> None:
    while True:
        print_header("PURCHASE ORDER MANAGEMENT")
        print("1. Create purchase orders from all low-stock items")
        print("2. View all purchase orders\n3. View draft orders\n4. Review and email a draft order")
        print("5. Mark order as sent (e.g. sent outside this program)")
        print("6. Mark sent order as received\n7. Cancel a draft order\n0. Return")
        choice = input("Select: ").strip()
        if choice == "1":
            create_low_stock_orders(data)
        elif choice == "2":
            list_orders(data)
        elif choice == "3":
            list_orders(data, "DRAFT")
        elif choice == "4":
            order = get_order(data)
            if order:
                if order["status"] != "DRAFT":
                    print("Only draft orders can be emailed.")
                else:
                    send_order_email(data, order)
        elif choice == "5":
            order = get_order(data)
            if order and order["status"] == "DRAFT":
                if yes_no("Confirm this order has been sent to the supplier by another method?"):
                    mark_order_sent(data, order, "manual")
            elif order:
                print("Only draft orders can be marked as sent.")
        elif choice == "6":
            order = get_order(data)
            if order:
                receive_order(data, order)
        elif choice == "7":
            order = get_order(data)
            if order and order["status"] == "DRAFT":
                if yes_no(f"Cancel draft {order['id']}?"):
                    order["status"] = "CANCELLED"
                    order["cancelled_at"] = timestamp()
                    save_data(data, "orders")
                    print("Draft order cancelled.")
            elif order:
                print("Only draft orders can be cancelled here.")
        elif choice == "0":
            return
        else:
            print("Invalid selection.")


# ---------------------------------------------------------------------------
# Email configuration and sending
# ---------------------------------------------------------------------------

def email_settings_menu(data: dict[str, Any]) -> None:
    config = data["settings"]["email"]
    print_header("EMAIL SETTINGS")
    print("The email password is NOT stored in JSON. It is read from an environment variable.")
    print("Press Enter to keep an existing setting.")
    for field, label in (("smtp_host", "SMTP server/host"),
                         ("sender", "Sender email address"),
                         ("username", "SMTP username"),
                         ("password_env", "Password environment variable name")):
        current = str(config.get(field, ""))
        value = input(f"{label} [{current}]: ").strip()
        if value:
            config[field] = value
    port_text = input(f"SMTP port [{config['smtp_port']}]: ").strip()
    if port_text:
        try:
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError
            config["smtp_port"] = port
        except ValueError:
            print("Invalid port; previous value retained.")
    config["use_starttls"] = yes_no("Use STARTTLS encryption?", default=True)
    if config.get("sender") and not valid_email(config["sender"]):
        print("Warning: sender address does not look valid.")
    save_data(data, "settings")
    print("Email settings saved. Set the password environment variable before sending.")


def make_email(data: dict[str, Any], order: dict[str, Any]) -> EmailMessage | None:
    supplier = find_record(data["suppliers"], order["supplier_id"])
    if not supplier or not supplier.get("email"):
        print("This supplier has no email address. Update the supplier record first.")
        return None
    if not valid_email(supplier["email"]):
        print("The supplier email address is invalid. Update the supplier record first.")
        return None
    contact = supplier.get("contact") or supplier["company"]
    message = EmailMessage()
    message["To"] = supplier["email"]
    message["From"] = data["settings"]["email"].get("sender", "")
    message["Subject"] = f"Purchase Order {order['id']} - Stock Replenishment"
    lines = [
        f"Dear {contact},", "",
        "Please find below our stock replenishment request.",
        f"Purchase Order: {order['id']}", "",
        f"{'Product':<30} {'Quantity':>8} {'Unit cost':>12} {'Line total':>12}",
        "-" * 66,
    ]
    for line in order["items"]:
        total = line["quantity"] * line["unit_cost"]
        lines.append(f"{line['name'][:30]:<30} {line['quantity']:>8} "
                     f"{currency(data, line['unit_cost']):>12} {currency(data, total):>12}")
    lines.extend([
        "", f"Estimated order total: {currency(data, order['total'])}", "",
        "Please confirm availability, pricing and the expected delivery date.",
        "", "Kind regards,", "Purchasing Department",
    ])
    message.set_content("\n".join(lines))
    return message


def send_order_email(data: dict[str, Any], order: dict[str, Any]) -> bool:
    if order.get("status") != "DRAFT":
        print("Only draft purchase orders can be emailed.")
        return False
    config = data["settings"]["email"]
    sender = config.get("sender", "")
    if not valid_email(sender):
        print("Configure a valid sender address in Email settings first.")
        return False
    password_variable = config.get("password_env") or "STOCK_EMAIL_PASSWORD"
    password = os.environ.get(password_variable, "")
    if not config.get("smtp_host") or not config.get("username") or not password:
        print("Email is not fully configured.")
        print(f"Set {password_variable} in your operating system environment, then retry.")
        return False
    message = make_email(data, order)
    if message is None:
        return False

    # Always show the complete message and require an explicit yes before sending.
    print_header("REVIEW SUPPLIER EMAIL")
    print(f"To:      {message['To']}")
    print(f"From:    {message['From']}")
    print(f"Subject: {message['Subject']}")
    print("-" * 72)
    print(message.get_content())
    print("-" * 72)
    if not yes_no("Send this email now?", default=False):
        print("Email not sent. Purchase order remains a DRAFT.")
        return False

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(config["smtp_host"], int(config["smtp_port"]), timeout=30) as server:
            server.ehlo()
            if config.get("use_starttls", True):
                server.starttls(context=context)
                server.ehlo()
            server.login(config["username"], password)
            server.send_message(message)
        order["status"] = "SENT"
        order["sent_at"] = timestamp()
        order["sent_method"] = "email"
        save_data(data, "orders")
        print("Email sent successfully. Purchase order marked as SENT.")
        return True
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        print(f"Email could not be sent: {exc}")
        print("The purchase order remains a DRAFT; check settings and retry.")
        return False


def send_low_stock_emails(data: dict[str, Any]) -> None:
    print_header("LOW-STOCK SUPPLIER ORDER REQUESTS")
    created = create_low_stock_orders(data)
    if not created:
        print("No new draft orders were created.")
        return
    print("\nEach supplier email will be displayed separately and requires confirmation.")
    for order in created:
        supplier = find_record(data["suppliers"], order["supplier_id"])
        print(f"\nOrder {order['id']} for {supplier['company']}:")
        send_order_email(data, order)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        data = load_data()
        create_daily_backup()
    except (DataFileError, OSError) as exc:
        print(f"Startup error: {exc}")
        return

    while True:
        print_header("STOCK MANAGEMENT SYSTEM")
        low_count = len(low_stock_items(data))
        print(f"Low-stock items currently detected: {low_count}")
        print(" 1. Stock management")
        print(" 2. Supplier management")
        print(" 3. Stock in (goods received)")
        print(" 4. Stock out (goods issued)")
        print(" 5. View stock inventory")
        print(" 6. Search stock items")
        print(" 7. Low stock report")
        print(" 8. Purchase order management")
        print(" 9. Create and email low-stock requests")
        print("10. Stock movement history")
        print("11. Inventory summary report")
        print("12. Email settings")
        print("13. Data backup and restore")
        print("14. Export reports to CSV")
        print(" 0. Save and exit")
        choice = input("\nSelect an option: ").strip()
        try:
            if choice == "1":
                stock_menu(data)
            elif choice == "2":
                supplier_menu(data)
            elif choice == "3":
                stock_transaction(data, "IN")
            elif choice == "4":
                stock_transaction(data, "OUT")
            elif choice == "5":
                show_stock(data)
            elif choice == "6":
                search_stock(data)
            elif choice == "7":
                low_stock_report(data)
            elif choice == "8":
                purchase_order_menu(data)
            elif choice == "9":
                send_low_stock_emails(data)
            elif choice == "10":
                movement_history(data)
            elif choice == "11":
                reports(data)
            elif choice == "12":
                email_settings_menu(data)
            elif choice == "13":
                data = data_backup_menu(data)
            elif choice == "14":
                csv_export_menu(data)
            elif choice == "0":
                save_data(data)
                print("All data saved. Goodbye.")
                break
            else:
                print("Invalid selection. Choose a number from the menu.")
        except (KeyboardInterrupt, EOFError):
            print("\nOperation cancelled. Returning to the main menu.")
        except DataFileError as exc:
            print(f"Data file error: {exc}")
        except OSError as exc:
            print(f"File error: {exc}")
        except (KeyError, TypeError, ValueError) as exc:
            print(f"Data or input error: {exc}")
        if choice != "0":
            try:
                pause()
            except (KeyboardInterrupt, EOFError):
                print()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nApplication closed.")
