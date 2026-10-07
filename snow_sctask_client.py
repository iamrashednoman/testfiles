import requests
from requests.auth import HTTPBasicAuth

# ==============================================================
# CONFIGURATION
# ==============================================================
SNOW_INSTANCE = "your_instance_name"  # e.g., "dev12345"
SNOW_USERNAME = "your_service_account"
SNOW_PASSWORD = "your_password"

BASE_URL = f"https://{SNOW_INSTANCE}.service-now.com/api/now/table"
AUTH = HTTPBasicAuth(SNOW_USERNAME, SNOW_PASSWORD)
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# Core fulfillment and reference tables required for Catalog Tasks
TARGET_TABLES = [
    "sc_task",       # Catalog Task
    "sc_req_item",   # Requested Item (RITM)
    "sc_request",    # Request (REQ)
    "sys_user",      # User Directory (requested_for target)
    "sys_user_group" # Assignment Groups
]


# ==============================================================
# 1. TEST DIRECT TABLE READ ACCESS
# ==============================================================
def check_table_access(table_name):
    """Checks HTTP status and record visibility for a single table."""
    url = f"{BASE_URL}/{table_name}"
    params = {
        "sysparm_limit": 1,
        "sysparm_fields": "sys_id"
    }
    
    try:
        res = requests.get(url, auth=AUTH, headers=HEADERS, params=params, timeout=15)
    except requests.exceptions.RequestException as e:
        return {
            "status": "ERROR",
            "http_code": None,
            "can_read": False,
            "records_visible": False,
            "details": str(e)
        }

    if res.status_code == 200:
        records = res.json().get("result", [])
        return {
            "status": "PASS",
            "http_code": 200,
            "can_read": True,
            "records_visible": len(records) > 0,
            "details": "Table is readable and visible." if len(records) > 0 else "Table readable, but 0 records returned (check Query Business Rules/empty table)."
        }
    elif res.status_code == 403:
        return {
            "status": "FORBIDDEN",
            "http_code": 403,
            "can_read": False,
            "records_visible": False,
            "details": "ACL restriction: User lacks read permission or rest_service role."
        }
    elif res.status_code == 401:
        return {
            "status": "UNAUTHORIZED",
            "http_code": 401,
            "can_read": False,
            "records_visible": False,
            "details": "Invalid credentials or account locked."
        }
    else:
        return {
            "status": f"HTTP {res.status_code}",
            "http_code": res.status_code,
            "can_read": False,
            "records_visible": False,
            "details": res.text
        }


# ==============================================================
# 2. TEST DOT-WALK READ ACCESS (sc_task -> requested_for)
# ==============================================================
def check_dotwalk_access():
    """Validates if the account can dot-walk across the request chain."""
    url = f"{BASE_URL}/sc_task"
    params = {
        "sysparm_limit": 1,
        "sysparm_fields": "number,request_item.number,request_item.request.number,request_item.request.requested_for",
        "sysparm_display_value": "all"
    }

    try:
        res = requests.get(url, auth=AUTH, headers=HEADERS, params=params, timeout=15)
        if res.status_code != 200:
            return False, f"HTTP {res.status_code}: {res.text}"

        records = res.json().get("result", [])
        if not records:
            return False, "Query succeeded but returned 0 records to test dot-walking."

        sample = records[0]
        dotwalk_val = sample.get("request_item.request.requested_for")
        
        if dotwalk_val is not None and dotwalk_val != "":
            return True, f"Successfully resolved dot-walk: {dotwalk_val.get('display_value') if isinstance(dotwalk_val, dict) else dotwalk_val}"
        else:
            return False, "Dot-walk field returned null (check sc_request / sys_user read ACLs or task relationships)."

    except requests.exceptions.RequestException as e:
        return False, str(e)


# ==============================================================
# MAIN RUNNER
# ==============================================================
if __name__ == "__main__":
    print(f"==================================================")
    print(f" ServiceNow Permission Audit: '{SNOW_USERNAME}'")
    print(f" Instance: https://{SNOW_INSTANCE}.service-now.com")
    print(f"==================================================\n")

    print("[1] Evaluating Direct Table Permissions:")
    print("-" * 50)
    
    all_passed = True
    for table in TARGET_TABLES:
        result = check_table_access(table)
        status_symbol = "✔" if result["status"] == "PASS" else "✖"
        print(f"{status_symbol} Table: {table:<16} | Status: {result['status']:<10} (HTTP {result['http_code']})")
        print(f"   Note: {result['details']}")
        if result["status"] != "PASS":
            all_passed = False

    print("\n[2] Evaluating Dot-Walk Chain (sc_task -> sc_request.requested_for):")
    print("-" * 50)
    dw_success, dw_message = check_dotwalk_access()
    print(f"{'✔' if dw_success else '✖'} Dot-walk status: {'SUCCESS' if dw_success else 'FAILED'}")
    print(f"   Details: {dw_message}\n")

    print("==================================================")
    if all_passed and dw_success:
        print("Verdict: The service account has full read and dot-walk access.")
    else:
        print("Verdict: Permission bottlenecks detected. Review the failures above.")
    print("==================================================")
